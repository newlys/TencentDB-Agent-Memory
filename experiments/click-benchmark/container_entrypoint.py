"""Expose mounted source as a distribution without installing a second code copy.

Click's version tests use importlib.metadata. Metadata is disposable, derived
from the mounted pyproject, and contains no source or benchmark answers.
"""
import os
from pathlib import Path
import sys
import tomllib

project_file = Path('/workspace/pyproject.toml')
if project_file.exists():
    project = tomllib.loads(project_file.read_text())['project']
    metadata_root = Path('/tmp/click-project-metadata')
    dist = metadata_root / 'click.dist-info'
    dist.mkdir(parents=True,exist_ok=True)
    (dist/'METADATA').write_text(f"Metadata-Version: 2.1\nName: {project['name']}\nVersion: {project['version']}\n")
    (dist/'top_level.txt').write_text('click\n')
    os.environ['PYTHONPATH'] = '/workspace/src:'+str(metadata_root)
os.execvp(sys.argv[1],sys.argv[1:])
