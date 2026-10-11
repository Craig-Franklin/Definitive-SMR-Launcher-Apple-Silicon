# Repository access and `main` protection

This public fork is owned by the personal GitHub account
[`Craig-Franklin`](https://github.com/Craig-Franklin). Its `main` branch is
governed by the active GitHub ruleset
[`Only Craig-Franklin may update main`](https://github.com/Craig-Franklin/Definitive-SMR-Launcher-Apple-Silicon/rules/24883525).
The ruleset targets only `refs/heads/main`.

The ruleset restricts updates to `main` and grants bypass solely to the
`Craig-Franklin` user account (GitHub user ID `24256404`). A pull request
merge updates `main`, so another account cannot merge a PR into `main` under
this rule. The ruleset also restricts branch deletion and force pushes, and
does not allow a fork sync to update `main` without bypass. The owner's bypass
mode is `always`, so Craig can push directly and can bypass those restrictions.

As checked on 11 October 2026, the collaborators API returned only Craig
(admin), with no pending collaborator invitations or deploy keys. The active
ruleset appeared in GitHub's effective rules for `main`, and GitHub reported
the branch as protected. These access facts should be checked again after any
permissions or ruleset change.

The GitHub settings enforce this policy; this file records it but cannot
enforce it. Public users can still view or fork the repository and propose
pull requests. A credential acting as Craig's account can exercise Craig's
bypass, and the account owner can edit or remove the ruleset. The rule controls
*which account* may update `main`; it does not require a separate manual
approval for each change.

To verify the current policy while authenticated as the owner, inspect the
ruleset, effective branch rules, and repository access:

```sh
gh api repos/Craig-Franklin/Definitive-SMR-Launcher-Apple-Silicon/rulesets/24883525
gh api repos/Craig-Franklin/Definitive-SMR-Launcher-Apple-Silicon/rules/branches/main
gh api repos/Craig-Franklin/Definitive-SMR-Launcher-Apple-Silicon/collaborators
gh api repos/Craig-Franklin/Definitive-SMR-Launcher-Apple-Silicon/invitations
gh api repos/Craig-Franklin/Definitive-SMR-Launcher-Apple-Silicon/keys
```
