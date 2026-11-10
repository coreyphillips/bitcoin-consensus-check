"""Resolve official Bitcoin Core release tags for the release workflow."""
import argparse
import json
import re
import subprocess
import sys
import urllib.request


UPSTREAM = "https://github.com/bitcoin/bitcoin.git"
API = "https://api.github.com/repos/bitcoin/bitcoin/git/tags/"
BASELINE_TAG = "v32.1"
BASELINE_COMMIT = "f0de87f040621468b956f01f526a43a010310501"
VERSION = re.compile(r"v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:\.(0|[1-9][0-9]*))?(?:rc([1-9][0-9]*))?")
SHA = re.compile(r"[0-9a-f]{40}")


def tags_to_check(refs, candidate):
    tags = {}
    for line in refs.splitlines():
        sha, ref = line.split()
        tag = ref.removeprefix("refs/tags/")
        if SHA.fullmatch(sha) and VERSION.fullmatch(tag.removesuffix("^{}")):
            tags[tag] = sha
    if tags.get(BASELINE_TAG + "^{}") != BASELINE_COMMIT:
        raise ValueError("upstream baseline tag does not match the frozen Core commit")
    if candidate is not None:
        if not VERSION.fullmatch(candidate) or candidate not in tags:
            raise ValueError("candidate must be an existing official Core release or RC tag")
        selected = [candidate]
    else:
        selected = []
        for tag in tags:
            version = VERSION.fullmatch(tag)
            if version is None or version[4] is None:
                continue
            number = tuple(int(part or 0) for part in version.groups()[:3])
            if number > (32, 1, 0) and tag.split("rc")[0] not in tags:
                selected.append(tag)
    result = []
    for tag in sorted(selected):
        if tag + "^{}" not in tags:
            raise ValueError(f"{tag} is not an annotated tag")
        result.append({"tag": tag, "object": tags[tag], "commit": tags[tag + "^{}"]})
    return result


def verify_tag(release):
    with urllib.request.urlopen(API + release["object"], timeout=30) as response:
        metadata = json.load(response)
    if not (metadata["sha"] == release["object"]
            and metadata["tag"] == release["tag"]
            and metadata["object"]["type"] == "commit"
            and metadata["object"]["sha"] == release["commit"]
            and metadata["verification"]["verified"] is True):
        raise ValueError(f"GitHub could not verify the official signed {release['tag']} tag")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", help="an exact upstream release or RC tag")
    parser.add_argument("--revision", required=True, help="companion repository commit")
    parser.add_argument("--skip-reports-for", metavar="OWNER/REPO",
                        help="skip completed reports in this repository (requires gh auth)")
    args = parser.parse_args()
    if not SHA.fullmatch(args.revision):
        parser.error("--revision must be a full commit hash")
    if args.candidate is not None and not VERSION.fullmatch(args.candidate):
        parser.error("expected a release tag such as v32.1 or v33.0rc1")
    if args.skip_reports_for and not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", args.skip_reports_for):
        parser.error("invalid repository name")
    refs = subprocess.check_output(["git", "ls-remote", "--tags", UPSTREAM], text=True, timeout=90)
    matrix = []
    for release in tags_to_check(refs, args.candidate):
        artifact = f"result-{release['tag']}-{release['object']}-{args.revision}"
        if args.skip_reports_for:
            # An artifact under this name is only published after a complete comparison.
            endpoint = f"repos/{args.skip_reports_for}/actions/artifacts"
            data = subprocess.check_output(
                ["gh", "api", "--method", "GET", endpoint, "-f", f"name={artifact}"],
                text=True, timeout=60,
            )
            if any(a["name"] == artifact and not a["expired"]
                   and a["workflow_run"]["head_sha"] == args.revision
                   and a["workflow_run"]["head_repository_id"] == a["workflow_run"]["repository_id"]
                   for a in json.loads(data)["artifacts"]):
                continue
        verify_tag(release)
        matrix.append(dict(release, artifact=artifact))
    print(json.dumps({"include": matrix}, separators=(",", ":")))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        sys.exit(str(error))
