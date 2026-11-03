"""Local trusted-operator CLI.

`ingest`/`recall` keep the experimental retrieval interface from docs/14
(including the old --db/--user/--agent form). `serve`/`init` manage the local
HTTP service described in docs/16 §4-5/§8. Scope is only ever taken from this
command line or the config file, never from request payloads.
"""
import argparse
import json
import sys
from pathlib import Path

from . import config as config_module
from . import service as service_module
from .core import LocalModel, Store, recall


def _parser():
    parser = argparse.ArgumentParser(
        prog='fishmemory',
        description='FishMemory experimental local memory service and retrieval CLI.')
    parser.add_argument('--config', help='JSON config path (also FISHMEMORY_CONFIG); '
                                         'default fishmemory.local.json then config.example.json')
    parser.add_argument('--db', help='SQLite path; overrides config db')
    parser.add_argument('--user', help='scope user_id; overrides config')
    parser.add_argument('--agent', help='scope agent_id; overrides config')
    commands = parser.add_subparsers(dest='command', required=True)

    ingest = commands.add_parser('ingest', help='store a JSON array of authorized synthetic events')
    ingest.add_argument('file')
    ingest.add_argument('--config', default=argparse.SUPPRESS,
                        help='JSON config path (default fishmemory.local.json then config.example.json)')

    recall_cmd = commands.add_parser('recall', help='run one local retrieval')
    recall_cmd.add_argument('query')
    recall_cmd.add_argument('--config', default=argparse.SUPPRESS,
                            help='JSON config path (default fishmemory.local.json then config.example.json)')

    serve = commands.add_parser('serve', help='run the local HTTP service on 127.0.0.1')
    serve.add_argument('--config', default=argparse.SUPPRESS,
                       help='JSON config path (default fishmemory.local.json then config.example.json)')
    serve.add_argument('--port', type=int, help='override service.port (0 selects an ephemeral port)')

    init = commands.add_parser('init', help='write local config from the template and create the bearer token')
    init.add_argument('--config', default=argparse.SUPPRESS,
                      help='target config path (default fishmemory.local.json)')
    init.add_argument('--force', action='store_true', help='overwrite an existing config')
    return parser


def _load_config(args):
    return config_module.load_config(getattr(args, 'config', None))


def _load_config_or_none(args):
    """Ingest can run with explicit --db/--user/--agent and no config file."""
    try:
        return _load_config(args)
    except config_module.ConfigError:
        if getattr(args, 'config', None):
            raise
        if args.command == 'ingest' and args.db and args.user and args.agent:
            return None
        raise


def main(argv=None):
    sys.stdout.reconfigure(encoding='utf-8')
    args = _parser().parse_args(argv)

    if args.command == 'serve':
        try:
            config = _load_config(args)
            if args.db:
                config.db = Path(args.db).expanduser().resolve()
            if args.user:
                config.user_id = args.user
            if args.agent:
                config.agent_id = args.agent
            if args.port is not None:
                if not 0 <= args.port <= 65535:
                    raise config_module.ConfigError('--port must be in [0, 65535]')
                config.service['port'] = args.port
            service_module.run_service(config)
        except config_module.ConfigError as error:
            print(f'fishmemory: {error}', file=sys.stderr)
            return 2
        except OSError as error:
            print(f'fishmemory: cannot start service: {error}', file=sys.stderr)
            return 1
        return 0

    if args.command == 'init':
        try:
            config = config_module.init_config(getattr(args, 'config', None), force=args.force)
            # Create the schema up front so a fresh service is not reported as
            # database_unavailable before the first archived event.
            Store(config.db).close()
        except config_module.ConfigError as error:
            print(f'fishmemory: {error}', file=sys.stderr)
            return 2
        print(json.dumps({'status': 'initialized', 'config': str(config.source),
                          'db': str(config.db),
                          'token_file': str(config.service['token_file'])},
                         ensure_ascii=False, indent=2))
        return 0

    try:
        config = _load_config_or_none(args)
    except config_module.ConfigError as error:
        print(f'fishmemory: {error}', file=sys.stderr)
        return 2
    db = Path(args.db) if args.db else config.db
    scope = (args.user or config.user_id, args.agent or config.agent_id)
    store = Store(db)
    try:
        if args.command == 'ingest':
            events = json.loads(Path(args.file).read_text(encoding='utf-8-sig'))
            result = [{'id': event['id'], 'status': store.ingest(scope, event)}
                      for event in events]
        else:
            model = LocalModel(config.model)
            result = recall(store, scope, args.query, model,
                            deadline_s=config.service['recall_deadline_s'])
    finally:
        store.close()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

