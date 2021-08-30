#!/usr/bin/python3
# Python 3.6+
"""Perform upstream release"""
import argparse
import datetime
import json
import os
import requests
import subprocess
import sys
from functools import lru_cache
from pathlib import Path

from launchpadlib.launchpad import Launchpad

from lp_create_bug import create_bug


# shell = partial(subprocess.check_output, shell=True, text=True)
PROJECT = "cloud-init"
SERVER = "production"
TODAY = datetime.date.today().strftime("%Y-%m-%d")
CACHE_PATH = Path.home() / ".cache" / Path(__file__).stem / "releases.json"
CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)

BUG_TEMPLATE = """\
== Release Notes ==

Cloud-init release {NEW_RELEASE} is now available

The {NEW_RELEASE} release:
 * spanned about {RELEASE_TIME_SPAN}
 * had {NUM_CONTRIBUTORS} contributors from {NUM_DOMAINS} domains
 * fixed {NUM_BUGS} Launchpad issues

Highlights:
  <TODO_SUMMARIZED_HIGHLIGHTS>

== Changelog ==
{CHANGELOG}
"""


def sh(text):
    return subprocess.check_output(text, shell=True, text=True).rstrip()


def affirmative(response: str) -> bool:
    return response.lower() == "y" or not response.strip()


@lru_cache
def launchpad_login():
    lp = Launchpad.login_with(PROJECT, service_root=SERVER, version="devel")
    return lp


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-v", "--version", required=True, help="Version to release")
    parser.add_argument(
        "stage",
        nargs="?",
        default="all",
        choices=[
            "all",
            "create-release-bug",
            "prepare-git-branch",
            "build-and-upload",
            "tag-release",
            "close-all-bugs",
            "upload-copr",
            "email",
            "purge-cache",
        ],
    )

    args = parser.parse_args()

    return args


class CachedDict:
    def __init__(self, version):
        self.version = str(version)
        if CACHE_PATH.exists():
            with open(CACHE_PATH, "r") as f:
                self.cache = json.load(f)
        else:
            self.cache = {}
        if self.version not in self.cache:
            self.cache[self.version] = {}

    def _save_cache(self):
        with open(CACHE_PATH, "w") as f:
            json.dump(self.cache, f)

    def __setitem__(self, key, item):
        self.cache[self.version][key] = item
        self._save_cache()

    def __getitem__(self, key):
        try:
            return self.cache[self.version][key]
        except KeyError:
            None

    def purge(self):
        self.cache[self.version] = {}
        self._save_cache()


def get_old_version(cache) -> str:
    likely_old_version = sh(
        "git describe --tags " "$(git log --grep='Release' -1 --pretty=format:'%h')"
    )
    response = input(f"Old version ({likely_old_version}): ")
    old_version = response.strip() if response.strip() else likely_old_version
    cache["old_version"] = old_version
    return old_version


def get_release_version(old_version: str) -> str:
    old_version_parts = old_version.split(".")
    old_year = int(old_version_parts[0])
    current_year = int(str(datetime.datetime.now().year)[2:])
    if old_year >= current_year:
        old_version_parts[-1] = str(int(old_version_parts[-1]) + 1)
        likely_release_version = ".".join(old_version_parts)
    else:
        likely_release_version = f"{current_year}.1"
    response = input(f"Release version ({likely_release_version}): ")
    return response.strip() if response.strip() else likely_release_version


def get_release_version_from_bug(lp, bug_id):
    summary = lp.bugs.getBugData(bug_id=bug_id)[0]["bug_summary"]
    return summary.split("Release ")[1].strip()


def get_next_version(current_version: str) -> str:
    current_version_parts = current_version.split(".")
    year = int(current_version_parts[0])
    minor = int(current_version_parts[1])
    if minor > 3:
        current_year = int(str(datetime.datetime.now().year)[2:])
        likely_next_version = f"{current_year + 1}.1"
    else:
        likely_next_version = f"{year}.{minor + 1}"
    response = input(f"Next version ({likely_next_version}): ")
    return response.strip() if response.strip() else likely_next_version


def is_point_release(version):
    return version.count(".") > 1


def check_changelog():
    if not os.path.exists("ChangeLog"):
        print(
            "Please run this script from the root directory of the cloud-init "
            "source tree"
        )
        sys.exit(1)


def checkout_main_and_pull():
    if sh("git branch --show-current") != "main":
        response = input("Change current branch to main (Y/n)? ")
        if not affirmative(response):
            print(
                "Change branch to main and pull upstream commits "
                "before running this script"
            )
            sys.exit(1)
        sh("git fetch")
        sh("git checkout main")

    if sh("git rev-parse HEAD") != sh("git rev-parse @{u}"):
        response = input("Pull latest upstream (Y/n)? ")
        if not affirmative(response):
            print("Pull upstream commits before running this script")
            sys.exit(1)
        sh("git pull")


def create_release_bug(old_version, release_version):
    release_time_span = sh(f"git log {old_version}..HEAD --pretty='%ar' | tail -n 1")[
        :-4
    ]
    num_contributors = sh(
        f"git log {old_version}..HEAD --pretty='%aN' " "| sort -u | wc -l"
    )
    num_domains = sh(f"git log {old_version}..HEAD --pretty='%aE' | sort -u | wc -l")
    num_bugs = sh(f"git log {old_version}..HEAD | log2dch | grep 'LP: #' | wc -l")
    changelog = sh(f"git log {old_version}..HEAD | log2dch |  sed 's/^   //g'")
    print("")
    print("")
    print(f"Summary: Release {release_version}")
    print("")
    bug_description = BUG_TEMPLATE.format(
        NEW_RELEASE=release_version,
        RELEASE_TIME_SPAN=release_time_span,
        NUM_CONTRIBUTORS=num_contributors,
        NUM_DOMAINS=num_domains,
        NUM_BUGS=num_bugs,
        CHANGELOG=changelog,
    )
    print(bug_description)
    print("")
    response = input("Create a new bug in Launchpad with these contents (Y/n)? ")
    bug_id = ""
    if affirmative(response):
        bug_id = create_bug(
            project=PROJECT,
            title=f"Release {release_version}",
            description=bug_description,
            server=SERVER,
            print_contents=False,
        )
        print(
            "Created bug at https://bugs.launchpad.net/cloud-init/+bug/"
            "{}".format(bug_id)
        )

    else:
        print(
            "Create upstream release bug manually by visiting "
            "https://bugs.launchpad.net/cloud-init/+filebug"
        )
        input("Press Enter to continue")
    return bug_id, changelog


def prepare_git_branch(old_version, release_version, bug_id, changelog):
    check_changelog()
    checkout_main_and_pull()

    response = input("Create new release branch (Y/n)? ")
    if affirmative(response):
        try:
            sh(f"git checkout -b upstream/{release_version}")
        except subprocess.CalledProcessError:
            print(
                f"Could not checkout upstream/{release_version}. "
                "Does it already exist?"
            )

        with open("ChangeLog", "r+") as f:
            content = f.read()
            f.seek(0, 0)
            f.write(f"{release_version}\n{changelog}\n\n{content}")
        sh(f'sed -i "s/{old_version}/{release_version}/" cloudinit/version.py')
        while not bug_id:
            bug_id = input(
                "Enter bug ID that was created for this upstream release: "
            ).strip()
        commit_msg = (
            f"Release {release_version}\n\n"
            f"Bump the version in cloudinit/version.py to {release_version} "
            "and update ChangeLog.\n\n"
            f"LP: #{bug_id}"
        )
        sh(f"git commit -a -m '{commit_msg}'")


def get_series(lp_project, release_version):
    all_series_names = [series.name for series in lp_project.series]
    default_series = "trunk"
    if is_point_release(release_version):
        possible_series = ".".join(release_version.split(".")[:-1])
        if possible_series in all_series_names:
            default_series = possible_series

    series = None
    while not series:
        series_name = input(f"Specify series to upload to ({default_series}): ")
        if not series_name.strip():
            series_name = default_series
        if series_name in all_series_names:
            for possible_series in lp_project.series:
                if possible_series.name == series_name:
                    return possible_series
        else:
            valid_series = ", ".join(all_series_names)
            print(f"Invalid series. Your options are {valid_series}")


def build_tarball_and_sign():
    print("Creating tarball...")
    tarball_path = sh("./tools/make-tarball")
    print(
        "Calling GPG to create tarball signature (this might involve "
        "a password prompt)..."
    )
    gpg_call = f"gpg --armor --sign --detach-sig {tarball_path}"
    # Since I often forget my password...keep retrying here :)
    result = subprocess.call(gpg_call.split())
    while result != 0:
        response = input(f"'{gpg_call}' failed. Try again (Y/n)? ")
        if affirmative(response):
            result = subprocess.call(gpg_call.split())
        else:
            sys.exit(1)
    return tarball_path


def upload_to_launchpad(tarball_path, release_version, bug_id, series):
    lp = launchpad_login()
    lp_project = lp.projects(PROJECT)

    # Grab the file contents for the upload
    with open(tarball_path, "rb") as f:
        tarball_content = f.read()
    signature_path = f"{tarball_path}.asc"
    with open(signature_path, "rb") as f:
        signature_content = f.read()
    tarball_name = os.path.basename(tarball_path)

    # Before we can upload a release tarball, we need a milestone
    # and release on Launchpad. Both may or may not already be
    # pre-existing.

    # Find the release in the project's releases collection.
    release = None
    for rel in lp_project.releases:
        if rel.version == release_version:
            release = rel
            break
    if not release:
        for milestone in lp_project.all_milestones:
            if milestone.name == release_version:
                release = milestone.createProductRelease(date_released=TODAY)
    if not release:
        release, series = create_milestone_and_release(release_version, series)

    # Do the actual upload
    release.add_file(
        filename=tarball_name,
        description="release tarball",
        file_content=tarball_content,
        content_type="application/x-gzip",
        file_type="Code Release Tarball",
        signature_filename=signature_path,
        signature_content=signature_content,
    )

    bug_description = lp.bugs.getBugData(bug_id=bug_id)[0]["description"]
    release_notes, changelog = bug_description.split("== Release Notes ==")[1].split(
        "== Changelog =="
    )
    release_notes = release_notes.strip()
    changelog = "\n".join(changelog.splitlines()[1:]).rstrip()
    release.release_notes = release_notes
    release.changelog = changelog

    release.lp_save()
    print("Release uploaded!")
    print("Releaese can be found on Launchpad at:")
    print(release.web_link)
    print("")
    return series


def upload_to_github(tarball_path, release_version):
    signature_path = f"{tarball_path}.asc"
    github_token = os.environ.get('GITHUB_TOKEN')
    if not github_token:
        # TODO...THIS DOESN'T SEEM TO BE WORKING!
        github_token = input('Enter Github token: ').strip()

    response = requests.post(
        'https://api.github.com/repos/canonical/cloud-init/releases',
        data=json.dumps({
            'tag_name': release_version,
            'body': f'Release {release_version}'
        }),
        headers={
            'Accept': 'application/vnd.github.v3+json',
            'Authorization': 'token {}'.format(github_token),
        },
    )

    response.raise_for_status()
    release_id = response.json()['id']

    for path, content_type in [
        (tarball_path, 'application/x-gzip'),
        (signature_path, 'application/octet-stream'),
    ]:
        filename = os.path.basename(path)
        with open(path, "rb") as f:
            response = requests.post(
                'https://uploads.github.com/repos/canonical/'
                f'cloud-init/releases/{release_id}/assets',
                data=f,
                params={'name': filename, 'label': filename},
                headers={
                    'Accept': 'application/vnd.github.v3+json',
                    'Authorization': 'token {}'.format(github_token),
                    'Content-Type': content_type,
                },
            )
            response.raise_for_status()


def create_milestone_and_release(release_version, series):
    milestone = series.newMilestone(name=release_version, date_targeted=TODAY)
    release = milestone.createProductRelease(date_released=TODAY)
    return release, series


def create_new_milestone(release_version, series):
    # Create new milestone
    print(
        "Now that the release has been uploaded to Launchpad, we "
        "should create a milestone for the next release"
    )
    response = input("Create next milestone now (Y/n)? ")
    if affirmative(response):
        next_version = get_next_version(release_version)
        if next_version in [
            milestone.name for milestone in series.all_milestones
        ]:  # noqa: E501
            print("Milestone already exists. Skipping...")
        else:
            series.newMilestone(name=next_version)


def build_and_upload(lp, release_version, bug_id):
    response = input("Build tarball for upload (Y/n)? ")
    # TODO...cache results of build
    if affirmative(response):
        series = get_series(lp.projects(PROJECT), release_version)
        tarball_path = build_tarball_and_sign()
        response = input("Upload to launchpad (Y/n)? ")
        if affirmative(response):
            upload_to_launchpad(tarball_path, release_version, bug_id, series)
        response = input("Create new milestone on launchpad? (Y/n) ")
        if affirmative(response):
            create_new_milestone(release_version, series)
        response = input("Upload to Github (requires Github TOKEN) (Y/n)?" )
        if affirmative(response):
            upload_to_github(tarball_path, release_version)


def tag_release(release_version):
    # Check that we're on main and (maybe) no changes
    response = input(f"Tag {release_version} release and push to github (Y/n)? ")
    if affirmative(response):
        tag_cmd = "git tag --annotate --sign -m 'Release {0}' {0}".format(
            release_version
        )
        upstream = sh('git remote -v | grep "canonical/cloud-init.*push"').split()[0]
        push_cmd = f"git push {upstream} {release_version}"

        if (
            f"Release {release_version}"
            not in sh("git show HEAD --pretty=oneline --no-patch")
            or sh("git rev-parse --abbrev-ref HEAD") != "main"
        ):
            print(
                "HEAD must be the release commit on main before tagging. "
                "Re-run script when fixed or run the following manually:\n"
                f"{tag_cmd}\n"
                f"{push_cmd}"
            )
            return

        print(f"Running: {tag_cmd}")
        sh(tag_cmd)

        print(f"Running: {push_cmd}")
        sh(push_cmd)


def close_all_bugs(old_version, release_version):
    response = input(f"Close all bugs from {old_version} to {release_version} (Y/n)? ")
    if affirmative(response):
        try:
            sh(f"git log {old_version}..{release_version} 2>&1 >/dev/null")
        except subprocess.CalledProcessError:
            print(
                f"Error obtaining commits between {old_version} and "
                f"{release_version}. Did you forget to tag the release?"
            )
            return
        bugs = sh(
            f"""git log {old_version}..{release_version} | grep "^[ ]*LP:" | sort -u | awk -F 'LP: #' '{{printf $2 " "}}'"""  # noqa: E501
        )
        sh(f"lp-bugs-released --server={SERVER} {PROJECT} " f"{release_version} {bugs}")
        print(f"Closed bugs: {bugs}")


def upload_copr(cache):
    response = input("Build and upload to COPR (Y/n)? ")
    if affirmative(response):
        print("Building Centos 8 package...")
        package_name = cache["rpm_path"]
        if not package_name:
            build_output = sh(
                "bash ./tools/run-container --source-package --unittest "
                "--artifacts=./srpm/ rockylinux/8"
            )
            output_name = build_output.split("redhat package '")[1][:-1]
            package_name = f"./srpm/{Path(output_name).stem}/.rpm"
            cache["rpm_path"] = package_name

        try:
            from copr.v3 import Client
        except ImportError:
            print("Copr library not found. You need to `pip install copr`. " "Aborting")
            sys.exit(1)

        try:
            client = Client.create_from_config_file()
        except Exception as e:
            raise Exception(
                "No copr config found or copr token has expired! "
                "Visit https://copr.fedorainfracloud.org/api/ , log in, and "
                "copy contents to ~/.config/copr"
            ) from e

        if PROJECT == "cloud-init" and SERVER == "production":
            client.build_proxy.create_from_file(
                ownername="@cloud-init",
                projectname="el-testing",
                path=package_name,
                buildopts={"chroots": ['epel-8-x86_64']}
            )


def main():
    args = parse_args()
    release_version = args.version
    cache = CachedDict(release_version)
    if args.stage == "purge-cache":
        cache.purge()
        print(f"Cache purged for version {release_version}")
        sys.exit(0)
    if (
        args.stage in ["all", "create-release-bug", "prepare-git-branch"]
        and cache["premerge-complete"] is not True
    ):
        old_version = cache["old_version"] or get_old_version(cache)
        if args.stage in ["all", "create-release-bug"]:
            bug_id, changelog = create_release_bug(old_version, release_version)
            cache["bug_id"] = bug_id
            cache["changelog"] = changelog
        if args.stage in ["all", "prepare-git-branch"]:
            prepare_git_branch(
                old_version, release_version, int(cache["bug_id"]), cache["changelog"]
            )
        if args.stage in ["all", "create-release-bug", "prepare-git-branch"]:
            print("Now push your branch and nag a teammate to review it")
            print(
                "In the meantime, go fill out the TODO section of the bug you "
                "created at "
                f"https://bugs.launchpad.net/cloud-init/+bug/{bug_id}"
            )
            print("When git branch has been merged, re-run this script.")
            cache["premerge-complete"] = True
            sys.exit(0)
    if args.stage == "all":
        print(
            "Do NOT proceed until the git branch for this upstream release "
            "has been merged!"
        )
        response = input("Has git branch been merged (y/N)? ")
        if response.lower() != "y":
            print("Re-run script once branch has been merged")
            sys.exit(1)
    checkout_main_and_pull()
    lp = launchpad_login()

    if args.stage in ["all", "tag-release"]:
        tag_release(release_version)

    if args.stage in ["all", "build-and-upload"]:
        build_and_upload(lp, release_version, int(cache["bug_id"]))

    if args.stage in ["all", "close-all-bugs"]:
        close_all_bugs(cache["old_version"], release_version)

    if args.stage in ["all", "upload-copr"]:
        upload_copr(cache)

    if args.stage in ["all", "email"]:
        bug_description = lp.bugs.getBugData(bug_id=int(cache["bug_id"]))[0]["description"]
        email = "\n".join(bug_description.splitlines()[2:]).replace(
            "== Changelog ==",
            "Thank you for your contributions,\nThe cloud-init team\n\n"
            "The full changelog is below",
        )
        print("Email this...")
        print("To: cloud-init@lists.launchpad.net")
        print(f"Subject: Release of cloud-init {release_version}")
        print("======================================================")
        print(email)
        print("======================================================")


if __name__ == "__main__":
    main()
