"""Package the repository plugin without local configuration, keys, databases or logs."""
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]
FILES = ['main.py', 'metadata.yaml', '_conf_schema.json', 'README.md',
         'docs/17-安装与宿主接入.md', *['fishmemory/' + name + '.py' for name in
         ('__init__', 'astrbot_adapter', 'persona', 'model_endpoint', 'writing', 'config', 'observe', 'core')]]


if __name__ == '__main__':
    output = ROOT / 'dist' / 'astrbot_plugin_fishmemory.zip'
    output.parent.mkdir(exist_ok=True)
    with ZipFile(output, 'w', ZIP_DEFLATED) as archive:
        # AstrBot 4.14.6 uses the very first member as the enclosing directory.
        archive.writestr('FishMemory-release/', b'')
        for name in FILES:
            archive.write(ROOT / name, 'FishMemory-release/' + name)
    print(output)
