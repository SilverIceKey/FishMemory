"""Generate reviewable host snippets, without overwriting host settings. ADR-021."""
import argparse
import json
import sys
from importlib.resources import files
from pathlib import Path

HOSTS = ('codex', 'claude-code', 'astrbot', 'hermes', 'opencode-v1', 'opencode-v2', 'generic')


def render(host, executable, config):
    if host not in HOSTS:
        raise ValueError('unsupported host')
    args = ['-m', 'fishmemory.mcp_server', '--config', str(config)]
    entry = {'command': str(executable), 'args': args}
    dump = lambda obj: json.dumps(obj, ensure_ascii=False, indent=2) + '\n'
    if host == 'codex':
        # JSON quoted strings/arrays used here are valid TOML basic strings.
        return 'codex.toml', '[mcp_servers.fishmemory]\ncommand = ' + json.dumps(str(executable), ensure_ascii=False) + '\nargs = ' + json.dumps(args, ensure_ascii=False) + '\n'
    if host == 'hermes':
        # JSON is a YAML subset; merge mcp_servers into ~/.hermes/config.yaml.
        return 'hermes.json', dump({'mcp_servers': {'fishmemory': entry}})
    if host.startswith('opencode-'):
        server = {'fishmemory': {'type': 'local', 'command': [str(executable), *args]}}
        return host + '.json', dump({'mcp': {'servers': server} if host.endswith('v2') else server})
    if host == 'astrbot':
        return 'astrbot.json', dump(entry)
    return host + '.json', dump({'mcpServers': {'fishmemory': entry}})


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', choices=(*HOSTS, 'all'), required=True)
    parser.add_argument('--config', required=True)
    parser.add_argument('--python', default=sys.executable)
    parser.add_argument('--output', required=True, help='new directory for snippets and skill')
    args = parser.parse_args(argv)
    config = Path(args.config).expanduser().resolve(strict=True)
    executable = Path(args.python).expanduser().resolve(strict=True)
    output = Path(args.output).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=False)
    for host in HOSTS if args.host == 'all' else [args.host]:
        name, content = render(host, executable, config)
        (output / name).write_text(content, encoding='utf-8')
    skill = output / 'skills' / 'fishmemory'
    skill.mkdir(parents=True)
    (skill / 'SKILL.md').write_text(files('fishmemory').joinpath('assets/fishmemory/SKILL.md').read_text(encoding='utf-8'), encoding='utf-8')
    print(f'Generated {output}; merge the appropriate snippet into host settings. No host settings changed.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
