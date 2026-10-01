"use client";

import * as RD from "@radix-ui/react-dialog";
import { useQuery } from "@tanstack/react-query";
import { Command } from "cmdk";
import {
  BookOpen,
  Database,
  FileSearch,
  FolderGit2,
  GitPullRequest,
  LayoutDashboard,
  MessagesSquare,
  Plus,
  Settings,
  Trophy,
  University,
  User,
} from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { get } from "@/lib/api";
import { useDebounced, useMe } from "@/lib/hooks";

type Suggestion = { type: string; title: string; subtitle: string | null; url: string };

const TYPE_ICON: Record<string, typeof Trophy> = {
  competition: Trophy,
  dataset: Database,
  project: FolderGit2,
  course: BookOpen,
  organization: University,
  user: User,
  thread: MessagesSquare,
};

export function CommandPalette({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  const router = useRouter();
  const me = useMe().data;
  const [q, setQ] = useState("");
  const dq = useDebounced(q, 180);
  const suggestions = useQuery({
    queryKey: ["suggest", dq],
    queryFn: () => get<Suggestion[]>("/search/suggest", { q: dq }),
    enabled: open && dq.trim().length >= 2,
    staleTime: 30_000,
  });

  const go = (href: string) => {
    onOpenChange(false);
    setQ("");
    router.push(href);
  };

  const actions = [
    { label: "Dashboard", href: "/dashboard", icon: LayoutDashboard, auth: true },
    { label: "Browse competitions", href: "/competitions", icon: Trophy },
    { label: "Browse datasets", href: "/datasets", icon: Database },
    { label: "Courses", href: "/learn", icon: BookOpen },
    { label: "Good first issues", href: "/open-source/issues", icon: GitPullRequest },
    { label: "New discussion", href: "/discussions/new", icon: Plus, auth: true },
    { label: "New project", href: "/projects/new", icon: Plus, auth: true },
    { label: "Host a competition", href: "/organize/new", icon: Plus, auth: true },
    { label: "Settings", href: "/settings", icon: Settings, auth: true },
  ].filter((a) => !a.auth || me);

  return (
    <RD.Root open={open} onOpenChange={onOpenChange}>
      <RD.Portal>
        <RD.Overlay className="fixed inset-0 z-50 bg-black/60 backdrop-blur-sm" />
        <RD.Content className="fixed left-1/2 top-[12vh] z-50 w-[calc(100vw-2rem)] max-w-xl -translate-x-1/2 overflow-hidden rounded-[var(--radius-xl)] border border-border bg-surface shadow-card">
          <RD.Title className="sr-only">Search and commands</RD.Title>
          <RD.Description className="sr-only">Search competitions, datasets, people and more, or jump to a page.</RD.Description>
          <Command shouldFilter={false} label="Command palette" className="flex flex-col">
            <Command.Input
              value={q}
              onValueChange={setQ}
              placeholder="Search competitions, datasets, courses, people…"
              className="h-12 w-full border-b border-border bg-transparent px-4 text-sm text-fg outline-none placeholder:text-subtle"
            />
            <Command.List className="max-h-[60vh] overflow-y-auto p-2">
              <Command.Empty className="px-3 py-6 text-center text-sm text-subtle">
                {dq.length >= 2 ? (suggestions.isFetching ? "Searching…" : "No matches.") : "Type to search."}
              </Command.Empty>
              {suggestions.data?.length ? (
                <Command.Group heading="Results" className="text-xs text-subtle [&_[cmdk-group-heading]]:px-2 [&_[cmdk-group-heading]]:py-1.5">
                  {suggestions.data.map((s) => {
                    const Icon = TYPE_ICON[s.type] ?? FileSearch;
                    return (
                      <Command.Item key={s.url} value={s.url} onSelect={() => go(s.url)}
                        className="flex cursor-pointer items-center gap-3 rounded-md px-3 py-2 text-sm text-fg data-[selected=true]:bg-surface-2">
                        <Icon className="h-4 w-4 text-subtle" />
                        <span className="min-w-0 flex-1 truncate">{s.title}</span>
                        <span className="text-xs capitalize text-subtle">{s.type}</span>
                      </Command.Item>
                    );
                  })}
                </Command.Group>
              ) : null}
              {dq.trim().length >= 2 ? (
                <Command.Item value={`search:${dq}`} onSelect={() => go(`/search?q=${encodeURIComponent(dq)}`)}
                  className="flex cursor-pointer items-center gap-3 rounded-md px-3 py-2 text-sm text-fg data-[selected=true]:bg-surface-2">
                  <FileSearch className="h-4 w-4 text-subtle" /> See all results for “{dq}”
                </Command.Item>
              ) : null}
              <Command.Group heading="Go to" className="mt-1 text-xs text-subtle [&_[cmdk-group-heading]]:px-2 [&_[cmdk-group-heading]]:py-1.5">
                {actions.map((a) => (
                  <Command.Item key={a.href} value={a.label} onSelect={() => go(a.href)}
                    className="flex cursor-pointer items-center gap-3 rounded-md px-3 py-2 text-sm text-fg data-[selected=true]:bg-surface-2">
                    <a.icon className="h-4 w-4 text-subtle" /> {a.label}
                  </Command.Item>
                ))}
              </Command.Group>
            </Command.List>
          </Command>
        </RD.Content>
      </RD.Portal>
    </RD.Root>
  );
}
