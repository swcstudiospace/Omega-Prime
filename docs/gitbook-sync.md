# GitBook sync

This repository includes a [GitBook Git Sync configuration](https://github.com/swcstudiospace/Omega-Prime/blob/main/.gitbook.yaml).
It selects `docs/` as the content root, `docs/README.md` as the landing page,
and `docs/SUMMARY.md` as navigation. The file is preparation, not evidence that
a GitBook space is connected or published.

## Connect once (dashboard)

1. An authorized space owner enables Git Sync and grants GitBook access to the
   GitHub repository.
2. Select `swcstudiospace/Omega-Prime` and the intended published branch. `main`
   is the canonical branch; an unmerged development branch is not the published
   edition.
3. Select the repository root so GitBook can read `.gitbook.yaml`; that file
   selects `./docs/`, rather than a second copy of the documentation.
4. Review the space's import and publish settings. Automatic updates and PR
   previews depend on the actual connected space configuration; this local
   workflow does not establish them.

## Edit in the repo

Once connected, maintain pages in this repository and resolve any edits made
on both sides before merging. Keep [SUMMARY.md](SUMMARY.md) aligned with the
canonical pages: every page exactly once, with resolving relative links.
The existing navigation consumer is `omega_prime/tests/test_docs.py`.

The [Greptile guide](greptile.md) links the single historical mirror under
`kb/`. The 21 synthesized mirror documents stay outside the `docs/` content
root and are not imported as current canonical GitBook pages.

Before delivery, run the repository's [local validation](local-validation.md)
and the navigation/catalog consumers:

```bash
python -m omega_prime.tooling.catalog --check
python -m pytest omega_prime/tests/test_docs.py omega_prime/tests/test_tool_catalog.py -q
```

Run these from the repository root in the installed development environment.
They check local navigation and generated inventory, not remote GitBook
authentication, synchronization or publication.
