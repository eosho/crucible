"""Allow the CLI to run through ``python -m crucible``."""

from .cli import main

raise SystemExit(main())
