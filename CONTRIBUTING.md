# Contributing to Halo

Thanks for helping! A few ground rules keep Halo safe and private.

## Build and run

```sh
sudo apt install python3-gi python3-gi-cairo gir1.2-gtk-4.0 python3-pytest
make run          # runs from the checkout with HALO_DRY_RUN=1
make test         # the full pytest suite
```

Always use `HALO_DRY_RUN=1` when running the full app during development. Only ever run shred code against files inside `tests/` temp folders or loop-mounted test images — never against real files.

## The no-network rule

Halo contains no networking code. Never import `socket`, `ssl`, `http`, `urllib`, `ftplib`, `smtplib`, `xmlrpc`, `requests` or anything similar, and never add analytics, crash reporting, update checks or sign-up flows. `tests/test_no_network.py` fails the build if such an import appears.

## Code style

- Python 3.10+ (Mint ships 3.12), GTK 4 through PyGObject, Cairo for drawing. No libadwaita.
- Every colour, size and timing lives in `halo/theme.py` and `halo/style.css` — no magic numbers in drawing code.
- Wrap every user-facing string in `_()`.
- Every source file starts with:

  ```
  # SPDX-License-Identifier: MIT
  # Copyright (c) 2026 Riya K
  ```

- Write tests alongside each module and run `make test` before opening a pull request.
- In the UI, call it the "DoD 5220.22-M 3-pass method". Never say "certified", "guaranteed unrecoverable" or "military-grade".
