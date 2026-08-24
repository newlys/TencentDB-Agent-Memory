# Trigger Real v1

`trigger-real-v1` is a provenance-first benchmark for deciding **when** a completed
agent task should be considered for Skill extraction and **whether** the extracted
knowledge creates measurable downstream value.

This directory intentionally does not contain generated conversations or a
pre-assigned `create/update/nothing` ratio. A task root is admitted only when it
can be traced to a public issue/PR, an immutable dataset revision, a repository
commit, and at least one native agent trajectory.

## Reproduce

```powershell
python -m pip install -r experiments/trigger-real-v1/requirements.txt
python experiments/trigger-real-v1/scripts/acquire.py --candidate-count 5000 --freeze-count 1000
python experiments/trigger-real-v1/scripts/discover_families.py
python experiments/trigger-real-v1/scripts/build_replay_queue.py
python experiments/trigger-real-v1/scripts/validate.py --strict
python experiments/trigger-real-v1/scripts/build_report.py
```

Third-party Parquet files are read with HTTP range requests. Local caches and
full native trajectories are excluded from Git. The committed manifests contain
stable source pointers and hashes, not republished bulk data.

## Release gates

- exactly 1,000 unique real task roots in the provisional selection;
- at least 5,000 unique candidates before freezing;
- every frozen root has a non-empty problem statement, base commit, repository
  license, deterministic evaluator metadata, and a public native trajectory;
- all rollouts of one root remain in the same split;
- no Skill family is accepted solely because an algorithm produced a cluster;
- unknown-license sources remain quarantined.

`selected-roots.jsonl` is not the final frozen release. The release publisher
requires completed WorkBuddy A/B runs and family review before it can create
`frozen-roots.jsonl`. `families.json` contains discovery candidates only. A candidate becomes an
accepted Skill family after chronological reuse and paired A/B utility review.
