"""Run the bundled, hash-pinned runtime independently of the working directory."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys


def main():
    if sys.version_info < (3, 11):
        print(json.dumps({'ok': False, 'error': {'code': 'PYTHON_VERSION', 'message': 'Python 3.11+ required.'}}))
        return 2
    root = Path(__file__).resolve().parents[1]
    archive = root / 'runtime.pyz'
    try:
        expected = json.loads((root / 'build-info.json').read_text(encoding='utf-8'))['runtime_sha256']
        if hashlib.sha256(archive.read_bytes()).hexdigest() != expected:
            raise ValueError('Runtime checksum mismatch')
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(json.dumps({'ok': False, 'error': {'code': 'INTEGRITY_ERROR', 'message': str(error)}}))
        return 2
    return subprocess.run([sys.executable, '-I', str(archive), *sys.argv[1:]], shell=False).returncode


if __name__ == '__main__':
    raise SystemExit(main())
