# Contributing

Thanks for considering a contribution. The project is small; short, focused issues and
pull requests land fastest.

## Reporting bugs and ideas

Open an issue with the **Bug report** or **Feature request** form. Before you paste
output, remove issue titles, project names and anything else private, and never paste your
Linear API key. Security problems go through the private channel in
[SECURITY.md](SECURITY.md), not an issue.

To keep spam out, accounts created in the last 24 hours cannot open issues or pull requests
or comment. If that stops you, try again a day later.

## Development setup

```sh
git clone https://github.com/SantyagoSeaman/cmux-linear-sidebar.git
cd cmux-linear-sidebar
python3 -m unittest discover -s tests -t .           # the whole suite, a few seconds
/usr/bin/python3 -m unittest discover -s tests -t .  # again on the oldest supported Python, 3.9
```

To try a change in cmux, run `scripts/dock-setup.sh --restart` from your clone: it stops
the running server and starts yours in the Dock's **Linear server** tab. [docs/architecture.md](docs/architecture.md)
explains how the pieces fit and which cmux behaviour they rely on.

## Ground rules

- **Standard library only.** The program must keep working with the Python that Apple's
  command line tools install, with nothing to `pip install`.
- **Tests for behaviour changes.** A change without a test that shows the new behaviour
  will not be taken. Tests must not talk to the real cmux or Linear; use the fakes in
  `tests/`.
- **Docs are part of the change.** Update `README.md` or `docs/architecture.md` in the same
  pull request when behaviour changes.
- **Keep private data out.** No API keys, tokens or real Linear data in code, tests,
  screenshots or descriptions.
- **Never publish a session link.** No `Claude-Session:` trailer, no claude.ai or
  chatgpt.com session URL, and no bare `session_*` id in a commit message or a pull request
  description.

## Sign-off (DCO)

We use the [Developer Certificate of Origin](https://developercertificate.org/). Add
`Signed-off-by: Your Name <you@example.com>` to each commit (`git commit -s`).

## Code of conduct

Participation is governed by the [Code of Conduct](CODE_OF_CONDUCT.md).
