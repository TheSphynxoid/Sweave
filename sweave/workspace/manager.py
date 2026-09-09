from __future__ import annotations

import asyncio
import os
import subprocess
from pathlib import Path
from typing import Any
from dataclasses import dataclass
from datetime import datetime


from sweave.platform import run_no_window


@dataclass
class WorktreeInfo:
    """Information about a git worktree."""
    path: Path
    branch: str
    task_id: str
    agent_name: str
    created_at: datetime
    pr_url: str | None = None
    pr_number: int | None = None


class WorktreeManager:
    """Manages git worktrees for specialist agents."""

    def __init__(self, base_path: str = ".worktrees", git_dir: str | None = None):
        self.base_path = Path(base_path).resolve()
        self.git_dir = Path(git_dir).resolve() if git_dir else Path.cwd()
        self.base_path.mkdir(parents=True, exist_ok=True)

    # ---- M1.9 step 2: alignment primitive --------------------

    def _is_git_dirty(self, worktree_path: Path) -> bool:
        """True iff ``worktree_path`` has uncommitted changes.

        ``git status --porcelain`` is the cheap path: an empty output
        means a clean tree. The M1.9 dirty-skip rule (per the
        commit-authority map ruling 2026-09-04) is: never stash-dance
        a working agent.
        """
        try:
            result = self._run_git(
                ["status", "--porcelain"], cwd=worktree_path
            )
            return bool(result.stdout.strip())
        except subprocess.CalledProcessError:
            return False
        except Exception:
            # Not a git repo (or git unavailable) -- treat as not-dirty
            # so ``align`` can take the no-integration-branch path
            # instead of erroring on a non-git directory.
            return False

    def align(
        self,
        worktree_path: Path,
        *,
        base_branch: str = "main",
    ) -> str:
        """M1.9 step 2: alignment primitive (R2's full protocol lands later).

        Rebase / merge the worktree's branch onto the integration
        branch's current state. The full protocol (drift budget,
        conflict resolution, freshness checks for shared-context
        files) is R2 scope; this step ships the primitive + the
        dirty-skip rule.

        Returns one of:
        * ``"skipped_dirty"`` -- uncommitted changes; never stash-dance
        * ``"no_integration_branch"`` -- the integration branch is
          missing or the worktree is not a git repo; soft no-op
        * ``"rebased"`` -- the rebase ran (whether or not it actually
          moved the branch is unobserved)
        * ``"noop"`` -- rebase is a no-op (already up to date)

        Raises: nothing. Callers branch on the returned string; this
        is the contract. ``align`` is intentionally non-raising so
        it can be called from a sweep loop without exception plumbing.
        """
        try:
            worktree_path = Path(worktree_path)
            if not worktree_path.exists():
                return "no_integration_branch"
            # Dirty-skip rule: never stash-dance a working agent.
            if self._is_git_dirty(worktree_path):
                return "skipped_dirty"
            # Verify the integration branch exists locally. (A
            # worktree that was never pushed doesn't have a remote
            # tracking branch; align on a missing branch is a no-op.)
            try:
                self._run_git(
                    ["rev-parse", "--verify", f"refs/heads/{base_branch}"],
                    cwd=worktree_path,
                )
            except subprocess.CalledProcessError:
                return "no_integration_branch"
            # Rebase. ``--autostash`` is intentionally NOT used -- the
            # dirty-skip rule above guarantees we never have anything
            # to autostash. If we get here dirty, something changed
            # under us; the rebase will fail safely with a non-zero
            # exit, which we report as ``skipped_dirty`` (conservative
            # -- the agent's state is suspect).
            try:
                self._run_git(
                    ["rebase", base_branch], cwd=worktree_path
                )
                return "rebased"
            except subprocess.CalledProcessError:
                # Abort the in-progress rebase so the agent's tree is
                # left in a clean state. (Stashing-on-rebase-failure
                # would violate the dirty-skip rule; we abort.)
                try:
                    self._run_git(
                        ["rebase", "--abort"], cwd=worktree_path
                    )
                except subprocess.CalledProcessError:
                    pass
                return "skipped_dirty"
        except Exception:
            # Catch-all so the sweep loop never crashes on a bad
            # worktree. The contract is the return string.
            return "no_integration_branch"

    def create_worktree(self, task_id: str, agent_name: str) -> WorktreeInfo:
        """Create a new worktree for a task/agent combination."""
        branch_name = f"sweave/{task_id}/{agent_name}"
        worktree_path = self.base_path / f"{task_id}-{agent_name}"
        
        # Create worktree with new branch
        self._run_git(["worktree", "add", "-b", branch_name, str(worktree_path)])
        
        info = WorktreeInfo(
            path=worktree_path,
            branch=branch_name,
            task_id=task_id,
            agent_name=agent_name,
            created_at=datetime.now(),
        )
        
        return info
    
    def remove_worktree(self, task_id: str, agent_name: str, force: bool = False) -> bool:
        """Remove a worktree."""
        worktree_path = self.base_path / f"{task_id}-{agent_name}"
        
        if not worktree_path.exists():
            return False
        
        try:
            if force:
                self._run_git(["worktree", "remove", "--force", str(worktree_path)])
            else:
                self._run_git(["worktree", "remove", str(worktree_path)])
            return True
        except subprocess.CalledProcessError:
            return False
    
    def list_worktrees(self) -> list[WorktreeInfo]:
        """List all sweave worktrees."""
        result = self._run_git(["worktree", "list", "--porcelain"])
        worktrees = []
        
        current = {}
        for line in result.stdout.strip().split("\n"):
            if not line:
                if current:
                    worktrees.append(self._parse_worktree(current))
                    current = {}
                continue
            
            if line.startswith("worktree "):
                current["path"] = Path(line[9:])
            elif line.startswith("branch "):
                current["branch"] = line[7:]
            elif line.startswith("HEAD "):
                current["head"] = line[5:]
        
        if current:
            worktrees.append(self._parse_worktree(current))
        
        # Filter to sweave worktrees
        return [w for w in worktrees if w.branch.startswith("sweave/")]
    
    def _parse_worktree(self, data: dict) -> WorktreeInfo:
        """Parse worktree info from git output."""
        path = data.get("path", Path())
        branch = data.get("branch", "")
        
        # Extract task_id and agent_name from branch: sweave/{task_id}/{agent_name}
        parts = branch.replace("sweave/", "").split("/")
        task_id = parts[0] if parts else "unknown"
        agent_name = parts[1] if len(parts) > 1 else "unknown"
        
        return WorktreeInfo(
            path=path,
            branch=branch,
            task_id=task_id,
            agent_name=agent_name,
            created_at=datetime.now(),  # Would need git log for actual date
        )
    
    def get_worktree_path(self, task_id: str, agent_name: str) -> Path | None:
        """Get worktree path for a task/agent."""
        worktrees = self.list_worktrees()
        for wt in worktrees:
            if wt.task_id == task_id and wt.agent_name == agent_name:
                return wt.path
        return None
    
    def create_pr(
        self,
        task_id: str,
        agent_name: str,
        title: str,
        body: str,
        base_branch: str = "main",
        token: str | None = None,
    ) -> str | None:
        """Create a PR for the worktree (uses gh CLI or GitHub API)."""
        worktree = self.get_worktree_path(task_id, agent_name)
        if not worktree:
            return None
        
        branch = f"sweave/{task_id}/{agent_name}"
        
        # Try gh CLI first
        if self._has_gh():
            return self._create_pr_gh(worktree, branch, title, body, base_branch)
        
        # Fallback to GitHub API
        if token:
            return self._create_pr_api(worktree, branch, title, body, base_branch, token)
        
        return None
    
    def _has_gh(self) -> bool:
        """Check if gh CLI is available."""
        try:
            run_no_window(["gh", "--version"], capture_output=True, check=True)
            return True
        except Exception:
            return False
    
    def _create_pr_gh(
        self,
        worktree: Path,
        branch: str,
        title: str,
        body: str,
        base_branch: str,
    ) -> str | None:
        """Create PR using gh CLI."""
        try:
            # Push branch
            self._run_git(["push", "origin", branch], cwd=worktree)
            
            # Create PR
            result = run_no_window(
                [
                    "gh", "pr", "create",
                    "--title", title,
                    "--body", body,
                    "--base", base_branch,
                    "--head", branch,
                ],
                cwd=worktree,
                capture_output=True,
                text=True,
                check=True,
            )
            return result.stdout.strip()
        except subprocess.CalledProcessError:
            return None
    
    def _create_pr_api(
        self,
        worktree: Path,
        branch: str,
        title: str,
        body: str,
        base_branch: str,
        token: str,
    ) -> str | None:
        """Create PR using GitHub REST API."""
        import urllib.request
        import json
        
        # Get repo info from git config
        try:
            remote_url = self._run_git(["config", "--get", "remote.origin.url"], cwd=worktree).stdout.strip()
            if "github.com" not in remote_url:
                return None
            
            # Parse owner/repo from URL
            if remote_url.startswith("git@"):
                repo_part = remote_url.split(":")[1]
            else:
                repo_part = remote_url.split("github.com/")[1]
            repo_part = repo_part.replace(".git", "")
            
            url = f"https://api.github.com/repos/{repo_part}/pulls"
            data = {
                "title": title,
                "body": body,
                "head": branch,
                "base": base_branch,
            }
            
            req = urllib.request.Request(
                url,
                data=json.dumps(data).encode(),
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            
            with urllib.request.urlopen(req) as response:
                result = json.loads(response.read().decode())
                return result.get("html_url")
        except Exception:
            return None
    
    def _run_git(self, args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
        """Run a git command."""
        return run_no_window(
            ["git", *args],
            cwd=cwd or self.git_dir,
            capture_output=True,
            text=True,
            check=True,
        )
    
    async def async_create_worktree(self, task_id: str, agent_name: str) -> WorktreeInfo:
        """Async wrapper for create_worktree."""
        return await asyncio.to_thread(self.create_worktree, task_id, agent_name)
    
    async def async_remove_worktree(self, task_id: str, agent_name: str, force: bool = False) -> bool:
        """Async wrapper for remove_worktree."""
        return await asyncio.to_thread(self.remove_worktree, task_id, agent_name, force)
    
    async def async_create_pr(
        self,
        task_id: str,
        agent_name: str,
        title: str,
        body: str,
        base_branch: str = "main",
        token: str | None = None,
    ) -> str | None:
        """Async wrapper for create_pr."""
        return await asyncio.to_thread(
            self.create_pr, task_id, agent_name, title, body, base_branch, token
        )