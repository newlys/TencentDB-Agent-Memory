/** Pilot-only wrapper around the real MemoryProxy app; no Skill mechanism edits. */
import { serve } from '@hono/node-server';
import { appendFileSync, mkdirSync, writeFileSync } from 'node:fs';
import { createHash, randomUUID } from 'node:crypto';
import { DEFAULT_CONFIG } from '../src/config.js';
import { createApp } from '../src/server.js';
import { initLogger } from '../src/report/log.js';
import { initLangfuse, shutdownLangfuse } from '../src/langfuse.js';

const key = process.env.DEEPSEEK_API_KEY;
const token = process.env.PILOT_PROXY_TOKEN;
if (!key || !token) throw new Error('Missing pilot credentials');
mkdirSync('/logs', { recursive: true });
const config = structuredClone(DEFAULT_CONFIG);
config.upstream = { url: 'https://api.deepseek.com/anthropic', apiKey: key, agents: {} };
config.injection = { enabled: false, injectors: [], assetReflection: { markerOptIn: false } };
config.extraction = { enabled: false, extractors: [] };
config.skillRuntime.allowLlmWrite = false;
config.sessionInit.enabled = false;
config.tdai.enabled = false;
config.auth.enabled = false;
config.rateLimit = { tpm: 0, qpm: 0 };
config.log = { ...config.log, file: '/logs/usage', level: 'debug' };
config.creditReport.url = '';
config.ccRequestRouting.enabled = true;
const langfuseEnabled = process.env.BENCHMARK_LANGFUSE_ENABLED === '1';
config.langfuse = langfuseEnabled
  ? { enabled:true, debug:true, host:process.env.LANGFUSE_BASE_URL!, publicKey:process.env.LANGFUSE_PUBLIC_KEY!, secretKey:process.env.LANGFUSE_SECRET_KEY! }
  : { ...config.langfuse, enabled:false, debug:false };
initLogger({ level: 'debug', filePath: '/logs/proxy', backend: 'console', rotate: config.log.rotate });
writeFileSync('/logs/effective-config.json', JSON.stringify({ ...config, upstream: { ...config.upstream, apiKey: '[REDACTED]' }, langfuse:{...config.langfuse,publicKey:'[REDACTED]',secretKey:'[REDACTED]'} }, null, 2));
if (langfuseEnabled && !await initLangfuse(config)) throw new Error('Langfuse initialization failed');
process.on('SIGTERM',async()=>{await shutdownLangfuse();process.exit(0);});
const app = createApp(config);
const record = (data: object) => appendFileSync('/logs/wire.jsonl', JSON.stringify({ timestamp: new Date().toISOString(), ...data })+'\n');
serve({ hostname: '0.0.0.0', port: 8096, fetch: async (request) => {
  const path = new URL(request.url).pathname;
  if (path === '/health') return app.fetch(request);
  if (!['/claude-code/pilot/v1/messages','/claude-code/pilot/v1/messages/count_tokens'].includes(path)) return new Response('pilot endpoint only', { status: 404 });
  const supplied = request.headers.get('x-api-key') || request.headers.get('authorization')?.replace(/^Bearer /,'');
  if (supplied !== token) return new Response('unauthorized', { status: 401 });
  const id = randomUUID();
  const raw = await request.clone().text();
  record({ event: 'request', id, path, sha256: createHash('sha256').update(raw).digest('hex'), body: JSON.parse(raw) });
  try {
    const response = await app.fetch(request);
    // The response clone is a tee: capture complete SSE without replacing forwarding.
    response.clone().text().then(body => record({ event:'response', id, status:response.status, body })).catch(error => record({ event:'capture_error', id, error:String(error) }));
    return response;
  } catch (error) {
    record({ event:'proxy_error', id, error:String(error) });
    return new Response('pilot proxy error', { status:502 });
  }
} });
