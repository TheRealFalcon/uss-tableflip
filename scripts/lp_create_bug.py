#!/usr/bin/python3
"""Create bugs in launchpad for the specified project"""

import argparse
from launchpadlib.launchpad import Launchpad
from launchpadlib import uris

BUG_IMPORTANCE = ['Low', 'Wishlist', 'Medium', 'High', 'Critical']
BUG_STATUS = ['New', 'Confirmed', 'Triaged', 'In Progress', 'Fix Committed']
CREATE_BUG_MESSAGE_TMPL = """
Creating a bug in {project} on server {server}:
Importance: {importance}
Status:     {status}
Tags:       {tags}
Title: {title}

{description}
"""


def create_bug(
    project, title, description, tag=None, importance=None, status='New',
    dry_run=False, server='production', print_contents=True
):
    lp = Launchpad.login_with(
        "uss-tableflip lp-bug-create",
        service_root=server,
        version='devel')

    project_name = project
    lp_project = lp.projects(project_name)
    if description and description.startswith('@'):
        with open(description[1:], 'r') as stream:
            description = stream.read()
    else:
        description = description
    create_args = {
        "target": lp_project,
        "title": title,
        "description": description,
        "tags": tag}

    if print_contents:
        print(CREATE_BUG_MESSAGE_TMPL.format(
            project=project_name, server=server, importance=importance,
            status=status, **create_args))
    if dry_run:
        return
    lp_bug = lp.bugs.createBug(**create_args)
    for task in lp_bug.bug_tasks:
        if task.bug_target_name == project_name:
            if importance:
                task.importance = importance
            if status:
                task.status = status
            task.lp_save()
    return lp_bug.id


def main():
    parser = argparse.ArgumentParser(description=__doc__)

    parser.add_argument('--title', required=True, action='store',
                        help="Bug title string")
    parser.add_argument('--tag', action='append', default=[],
                        help="Specify optional tag to add to the bug.")
    parser.add_argument('--importance', action='store', default=None,
                        choices=BUG_IMPORTANCE, help="Bug importance value")
    parser.add_argument('--status', action='store', default='New',
                        choices=BUG_STATUS, help="Bug status value")
    parser.add_argument('--description', action='store', required=True,
                        help="Bug description string or @file")
    parser.add_argument('--dry-run', action='store_true', default=False,
                        help='only report what would be done')
    parser.add_argument('--server', action='store', default='production',
                        choices=['staging', 'qastaging', 'production'],
                        help='Launchpad server to use.')
    parser.add_argument('project', action='store', default=None,
                        help='The project to use.')

    args = parser.parse_args()

    bug_id = create_bug(
        project=args.project,
        title=args.title,
        description=args.description,
        tag=args.tag,
        importance=args.importance,
        status=args.status,
        dry_run=args.dry_run,
        server=args.server
    )

    print('=========================')
    print('Created bug number: {}'.format(bug_id))


if __name__ == '__main__':
    main()
