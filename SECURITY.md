# Security policy

Halo overwrites and deletes files, so security reports matter a lot to us.

**Please report vulnerabilities privately** by email to **riyak1@proton.me**, not in public GitHub issues. Include the Halo version (`halo --version`), your Linux Mint edition, and steps to reproduce. Please don't include real private file names or paths — a made-up example is enough.

You'll get a reply within a few days. Fixes are released as a new `.deb` on the GitHub Releases page, with credit if you'd like it.

In scope: the privileged helper (`/usr/libexec/halo/halo-helper`) and its polkit policy, protected-path checks, symlink handling, the shred engine and trace cleanup, and anything that could make Halo touch a file the user did not confirm.
