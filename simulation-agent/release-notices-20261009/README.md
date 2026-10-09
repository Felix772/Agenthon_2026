# Track 3 notice layer

This build adds the upstream ABIDES BSD-3-Clause license, the organizer adapter's
MIT license, and their source manifest to the frozen scalar simulator. It changes
no simulator code, dependency, command, environment variable, or working directory.
The files are installed at `/usr/share/licenses/agenthon-track3/`.

Before building, verify that the local parent tag resolves to
`sha256:a12731a118178e10469608ec92172bc7db4b799a2b4ad8cd2b1acf939dd053c9`.
Use this directory as the build context, with `--network none --pull=false`.
`notices/SOURCES.json` records immutable license sources and file checksums.

The validated local image is `simulation-agent:scalar-notices-20261009-v3`, ID
`sha256:2c88bb849a19a485a4964fa343ef9a18fd144ce77e76f69394315e2691649ec1`.
Its 23 parent layers are unchanged; the two new layers only copy the notices
and set their directory/file permissions. The image adds 5,298 bytes.

Validation in `project-evidence/t3-notices-20261009-v3/` at the workspace root
confirmed identical runtime configuration, all 9,424 inventoried runtime and
dependency files, and all 15 installed Python package names/versions. The image
is `linux/amd64`, carries interface `2.0`, and declares no Docker volume. UID
65534 can read the notices and verify both source hashes.

The exact image passed the current Linux public developer verifier (g0–g3)
on `t3-as01-base-mix` and `t3-gbatch-homog-4`. Both container runs exited 0
under the published restricted runtime settings, and all ten trace/ledger
Parquet files matched the corresponding pre-notice outputs byte for byte.
These checks do not establish official timing or a new leaderboard score.
The earlier complete 426-run performance experiment was not repeated.

The first two notice-image attempts failed the non-root notice-read check:
`COPY --chmod=644` also made newly created destination directories mode 0644.
Their complete logs and identities remain under
`project-evidence/t3-notices-20261009/` and
`project-evidence/t3-notices-20261009-v2/`. The final Dockerfile explicitly
sets directories to 0755 and notice files to 0644.

This folder does not publish an image or contain a submission package.
Existing Python, native dependency, and operating-system license files remain
in their original locations; the two missing upstream notices are now present.

Before a subsequent code change, consult the current README and applicable
AGENTS.md, CONTRIBUTING.md, SUBMISSION_CLI.md, and linked rules in all five
required repositories:

- https://github.com/Agenthon-2026/Agenthon2026-public
- https://github.com/Agenthon-2026/track1-coding-public
- https://github.com/Agenthon-2026/track2-forecasting-public
- https://github.com/Agenthon-2026/track3-simulation-public
- https://github.com/Agenthon-2026/track4-analysis-public
