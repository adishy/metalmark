"""Compare contracts/openapi.yaml against the spec the API actually serves.

The checked-in contract is what the frontend mocks are built against, so if it
drifts from the live app it is worse than absent — it is confidently wrong. This
compares them *semantically* (both sides parse to the same JSON structure), so
key order and YAML formatting are irrelevant and only real contract changes fail.

Lives here rather than inline in the CI workflow so that the same check can be run
locally — ``scripts/verify.sh contract`` — instead of only existing inside a YAML
heredoc where nobody can run it.

Usage (from inside the api container, where the contract has been copied to /tmp):
    python scripts/openapi_drift_check.py [path-to-contract.yaml]
    python scripts/openapi_drift_check.py --export > contracts/openapi.yaml

The second form is how you *fix* drift: it writes the live spec to stdout as
YAML (the container cannot reach the repo, so stdout is the seam). Regenerate,
read the diff, commit it.

  OPENAPI_LIVE_URL  default http://localhost:8000/openapi.json
"""

import json
import os
import pathlib
import sys
import urllib.request

import yaml

LIVE = os.environ.get("OPENAPI_LIVE_URL", "http://localhost:8000/openapi.json")
CHECKED = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/checked-openapi.yaml")


def normalize(obj):
    # Round-trip both sides through JSON so tuples/Decimals and any int-vs-float
    # quirk compare identically.
    return json.loads(json.dumps(obj, default=str))


def walk(a, b, path=""):
    """Yield a line per difference, so the output names what actually changed."""
    if isinstance(a, dict) and isinstance(b, dict):
        for key in sorted(set(a) | set(b)):
            if key not in b:
                yield f"{path}.{key}: missing from the checked-in file"
            elif key not in a:
                yield f"{path}.{key}: extra in the checked-in file"
            else:
                yield from walk(a[key], b[key], f"{path}.{key}")
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            yield f"{path}: {len(a)} live vs {len(b)} checked in"
        else:
            for i, (x, y) in enumerate(zip(a, b)):
                yield from walk(x, y, f"{path}[{i}]")
    elif a != b:
        yield f"{path}: live={a!r} checked={b!r}"


def fetch():
    return normalize(json.loads(urllib.request.urlopen(LIVE).read()))


def main() -> int:
    if "--export" in sys.argv:
        # sort_keys=False: the checked-in file keeps the API's own key order
        # (openapi, info, paths — and within an operation, tags/summary/…), so
        # preserving it here keeps the diff to the lines that really changed.
        #
        # width=100 is not a style preference: the checked-in file was generated
        # at this width, and PyYAML's default is 80. Regenerating without it
        # re-wraps every long description in the file and buries the two lines
        # that actually changed under a few hundred that did not.
        sys.stdout.write(
            yaml.safe_dump(fetch(), sort_keys=False, allow_unicode=True, width=100)
        )
        return 0

    live = fetch()
    checked = normalize(yaml.safe_load(CHECKED.read_text()))

    if live == checked:
        print(f"{CHECKED} matches the live {LIVE}")
        return 0

    print(f"DRIFT: {CHECKED} does not match the live {LIVE}", file=sys.stderr)
    print("Re-export the served spec to YAML and commit it:", file=sys.stderr)
    print("  docker compose cp scripts/openapi_drift_check.py api:/tmp/openapi_drift_check.py", file=sys.stderr)
    print("  docker compose exec -T api python /tmp/openapi_drift_check.py --export"
          " > contracts/openapi.yaml\n", file=sys.stderr)

    diffs = list(walk(live, checked))
    for line in diffs[:25]:
        print(" -", line, file=sys.stderr)
    if len(diffs) > 25:
        print(f" ... and {len(diffs) - 25} more", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
