#!/usr/bin/env python3
"""Build the chyron commit split as objects, without touching the worktree or main.

The chyron key work was swallowed by `2f41abf` ("Add coding agent telemetry and
response controls to OpenDeck"), which is already pushed, and another agent has
uncommitted work in this repo. So nothing visible happens here: the replacement
commits are assembled in a scratch index and parked on a branch. Moving `main`
onto them is a separate, single, reversible step.

    python3 scripts/split_deck_commits.py --dry-run   # report what would move
    python3 scripts/split_deck_commits.py             # park the commits on a branch
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess

REPO = Path(os.environ.get('STREAMDECK_ROOT', '/media/rspectre/Storage/workspace/streamdeck'))
BASE = '7c81f5f'        # before either agent commit
CODE = '2f41abf'        # their code commit, which swallowed the chyron work
DOCS = 'd0c0014'        # their docs commit (currently HEAD)
BRANCH = 'split/chyron-work'
SCRATCH_INDEX = '/tmp/split-chyron.index'

SPEC = 'opendeck-layout.json'
PROFILE = 'opendeck/profiles/sd-CL37L2A01125/01 - broadcast.json'
TEST = 'tests/test_build_opendeck.py'
POSITIONS = ('27', '28', '29')
ICONS = [f'images/controls/chyron-{agent}-{state}.svg'
         for agent in ('codex', 'claude', 'hermes')
         for state in ('off', 'on', 'unknown')]

MESSAGE = ('Add chyron toggle keys for Codex, Claude and Hermes on page 1\n'
           '\n'
           'Columns 27-29 were the Crickets/Laughs/Sad sound effects; they are\n'
           '`item` controls on the Chyron scene now, so a press shows or hides that\n'
           "agent's chyron. The key art is drawn in the house style by\n"
           'livestream_chyrons/scripts/make_deck_icons.py, and the broadcast page test\n'
           'asserts the new wiring instead of the old sound-effect copies.\n')


def git(*args, env=None, input_bytes=None, check=True) -> str:
    proc = subprocess.run(['git', *args], cwd=REPO, env={**os.environ, **(env or {})},
                          input=input_bytes, capture_output=True)
    if check and proc.returncode:
        raise SystemExit(f'git {" ".join(args)} failed: {proc.stderr.decode().strip()}')
    return proc.stdout.decode()


def show(ref: str, path: str) -> str:
    return git('show', f'{ref}:{path}')


def bundled_icons() -> list[str]:
    """The generated copies of the chyron key art, addressed by content hash."""
    return [f'opendeck/images/hackparty/{hashlib.sha256((REPO / icon).read_bytes()).hexdigest()}.svg'
            for icon in ICONS]


def without_chyron_json(committed: str, base: str, kind: str) -> str:
    """The committed file with the base commit's three buttons/keys put back."""
    doc, original = json.loads(committed), json.loads(base)
    if kind == 'spec':
        page_committed = doc['devices']['CL37L2A01125']['buttons']['0']
        page_base = original['devices']['CL37L2A01125']['buttons']['0']
        for position in POSITIONS:
            page_committed[position] = page_base[position]
    else:
        keys_base = {k['context']: k for k in original['keys']}
        doc['keys'] = [keys_base[k['context']] if k['context'] in {f'Keypad.{p}.0' for p in POSITIONS}
                       else k for k in doc['keys']]
    return json.dumps(doc, indent=2, ensure_ascii=False) + '\n'


def without_chyron_test(committed: str) -> str:
    """Undo the assertion change that came with the chyron keys."""
    mine = """        for index, label in zip((18, 19, 26), ('Rap Horn', 'Winner', 'Vibing')):
            copied = broadcast['keys'][index]
            original = next(k for k in soundboard['keys'] if k and k['states'][0]['text'] == label)
            self.assertEqual(copied['states'], original['states'])
            self.assertEqual({k:v for k,v in copied['settings'].items() if not k.startswith('_label_')}, {k:v for k,v in original['settings'].items() if not k.startswith('_label_')})
        # Columns 27-29 used to be the Crickets/Laughs/Sad sound effects. They are
        # chyron toggles now: the same `item` control the camera keys use, on the
        # Chryons scene, so a press shows or hides that agent's chyron.
        for index, agent in zip((27, 28, 29), ('codex', 'claude', 'hermes')):
            key = broadcast['keys'][index]
            self.assertEqual(key['settings']['kind'], 'item')
            self.assertEqual(key['settings']['scene'], 'Chryons')
            self.assertEqual(key['settings']['source'], f'Token Chyron - {agent.title()}')
            self.assertEqual(key['states'][0]['text'], agent.title())
            self.assertEqual(len(key['states']), 3)
"""
    theirs = """        for index, label in zip((18, 19, 26, 27, 28, 29), ('Rap Horn', 'Winner', 'Vibing', 'Crickets', 'Laughs', 'Sad')):
            copied = broadcast['keys'][index]
            original = next(k for k in soundboard['keys'] if k and k['states'][0]['text'] == label)
            self.assertEqual(copied['states'], original['states'])
            self.assertEqual({k:v for k,v in copied['settings'].items() if not k.startswith('_label_')}, {k:v for k,v in original['settings'].items() if not k.startswith('_label_')})
"""
    if mine not in committed:
        raise SystemExit('the chyron test block is not where it was expected - aborting')
    return committed.replace(mine, theirs)


def stage_content(path: str, content: str, env) -> None:
    sha = git('hash-object', '-w', '--stdin', env=env, input_bytes=content.encode()).strip()
    git('update-index', '--add', '--cacheinfo', f'100644,{sha},{path}', env=env)


def their_tree_without_chyron(ref: str, env) -> str:
    git('read-tree', f'{ref}^{{tree}}', env=env)
    for path in ICONS + bundled_icons():
        git('update-index', '--force-remove', path, env=env)
    stage_content(SPEC, without_chyron_json(show(ref, SPEC), show(BASE, SPEC), 'spec'), env)
    stage_content(PROFILE, without_chyron_json(show(ref, PROFILE), show(BASE, PROFILE), 'keys'), env)
    stage_content(TEST, without_chyron_test(show(ref, TEST)), env)
    return git('write-tree', env=env).strip()


def metadata(ref: str) -> dict:
    """The original author, committer and full message, so the split keeps attribution."""
    raw = git('show', '-s', '--format=%an%n%ae%n%at%n%cn%n%ce%n%ct%n%B', ref).rstrip('\n')
    parts = raw.split('\n')
    author_name, author_email, authored, committer_name, committer_email, committed = parts[:6]
    message = '\n'.join(parts[6:]).strip() + '\n'
    return {'env': {'GIT_AUTHOR_NAME': author_name, 'GIT_AUTHOR_EMAIL': author_email,
                    'GIT_AUTHOR_DATE': f'@{authored} +0000',
                    'GIT_COMMITTER_NAME': committer_name, 'GIT_COMMITTER_EMAIL': committer_email,
                    'GIT_COMMITTER_DATE': f'@{committed} +0000'},
            'subject': message.splitlines()[0],
            'message': message}


def commit_tree(tree: str, parent: str, env, message: str | None = None) -> str:
    return git('commit-tree', tree, '-p', parent, env=env,
               input_bytes=(message or '').encode()).strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run', action='store_true', help='report only')
    args = parser.parse_args()

    scratch = {**os.environ, 'GIT_INDEX_FILE': SCRATCH_INDEX}
    head_tree = git('rev-parse', f'{DOCS}^{{tree}}').strip()

    # A: their code work with the chyron pieces backed out.
    tree_a = their_tree_without_chyron(CODE, scratch)
    code = metadata(CODE)
    commit_a = commit_tree(tree_a, BASE, {**scratch, **code['env']}, code['message'])

    # B: their docs commit, with the same pieces backed out.
    tree_b = their_tree_without_chyron(DOCS, scratch)
    docs = metadata(DOCS)
    commit_b = commit_tree(tree_b, commit_a, {**scratch, **docs['env']}, docs['message'])

    # C: the chyron work, whose tree is the current HEAD exactly.
    commit_c = commit_tree(head_tree, commit_b, {**scratch, **docs['env']}, MESSAGE)

    print(f'A {commit_a[:9]}  {code["subject"]}')
    print(f'B {commit_b[:9]}  {docs["subject"]}')
    print(f'C {commit_c[:9]}  {MESSAGE.splitlines()[0]}')
    print(f'\nfinal tree == current HEAD tree: {git("rev-parse", "HEAD^{tree}").strip() == head_tree}')
    if args.dry_run:
        print('dry run: nothing written')
        return 0
    git('update-ref', f'refs/heads/{BRANCH}', commit_c)
    print(f'parked on {BRANCH}; main untouched')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
