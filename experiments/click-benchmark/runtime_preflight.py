"""Start/stop both runnable transports without invoking an Agent or an LLM."""
import argparse
import datetime as dt
import json
from pathlib import Path
import subprocess
import sys
import uuid

import bench
import session_driver as driver


def stop_baseline(service):
    for process in reversed(service.get('processes', [])):
        process.terminate()
        try:
            process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            process.kill()
    for handle in service.get('handles', []):
        handle.close()
    if service.get('network'):
        subprocess.run(['docker','network','rm',service['network']],capture_output=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--langfuse-config',type=Path,default=driver.LF_DEFAULT)
    parser.add_argument('--run',default='runtime-preflight-'+dt.datetime.now().strftime('%Y%m%d-%H%M%S'))
    args = parser.parse_args()
    report = {'run':args.run,'started_at':driver.now(),'paid_calls':False,'agent_started':False,'checks':{}}
    with bench.locked(args.run) as rd:
        root = rd/'runtime-preflight'
        token = uuid.uuid4().hex
        proxy = network = None
        try:
            proxy,network = driver.start_proxy(args.run,root/'no-skill',args.langfuse_config,token)
            report['checks']['no-skill'] = {'status':'READY','proxy_image':driver.image(bench.image_tag('no_skill_proxy'))}
        finally:
            if proxy:
                subprocess.run(['docker','stop','-t','20',proxy],capture_output=True)
                subprocess.run(['docker','rm',proxy],capture_output=True)
            if network:
                subprocess.run(['docker','network','rm',network],capture_output=True)
        service = None
        try:
            service = driver.start_baseline(args.run,root/'baseline',args.langfuse_config)
            report['checks']['baseline'] = {
                'status':'READY', 'core_port':service['core_port'],'proxy_port':service['proxy_port'],
                'native_skill_listing_preflight':True,
            }
        finally:
            if service:
                stop_baseline(service)
        report.update(status='PASS',ended_at=driver.now())
        bench.write(bench.ROOT/'reports/runtime-preflight.json',report)
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(json.dumps({'status':'FAIL','error':str(error),'paid_calls':False},ensure_ascii=False,indent=2),file=sys.stderr)
        raise
