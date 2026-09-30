"""Docker health probe and explicit recovery of permanent Telegram failures."""
from __future__ import annotations

import argparse

from .config import load_config
from .runtime_storage import RuntimeStorage


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--reset-blocked', action='store_true',
        help='After fixing Telegram configuration, re-enable permanently blocked notifications')
    args = parser.parse_args(argv)
    config = load_config()
    storage = RuntimeStorage(config.sqlite_path, database_url=config.database_url)
    try:
        if args.reset_blocked:
            print(f'Re-enabled {storage.reset_blocked_notifications()} notifications')
            return 0
        errors = storage.health_errors(config)
    except Exception:
        print('Unhealthy: runtime state unavailable')
        return 1
    if errors:
        print('\n'.join(errors))
        return 1
    print('Healthy: scheduler ticks current; no overdue candles or blocked deliveries')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
