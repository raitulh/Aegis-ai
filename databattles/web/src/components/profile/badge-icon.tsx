import {
  Award,
  BadgeCheck,
  BookOpen,
  Bot,
  Brain,
  Code,
  Compass,
  Cpu,
  Crown,
  Database,
  Flame,
  Gem,
  GitBranch,
  GitMerge,
  GitPullRequest,
  GraduationCap,
  Heart,
  HeartHandshake,
  Lightbulb,
  Map as MapIcon,
  Medal,
  MessageCircle,
  MessageSquare,
  Mountain,
  PartyPopper,
  Puzzle,
  Rocket,
  Shield,
  ShieldCheck,
  Sparkles,
  Star,
  Swords,
  Target,
  Trophy,
  Users,
  Zap,
  type LucideIcon,
} from "lucide-react";

import { cn } from "@/lib/cn";

/** Badge definitions store a lucide icon name (kebab-case). Unknown names fall back to Award. */
const ICONS: Record<string, LucideIcon> = {
  award: Award,
  "badge-check": BadgeCheck,
  "book-open": BookOpen,
  bot: Bot,
  brain: Brain,
  code: Code,
  compass: Compass,
  cpu: Cpu,
  crown: Crown,
  database: Database,
  flame: Flame,
  gem: Gem,
  "git-branch": GitBranch,
  "git-merge": GitMerge,
  "git-pull-request": GitPullRequest,
  "graduation-cap": GraduationCap,
  heart: Heart,
  "heart-handshake": HeartHandshake,
  lightbulb: Lightbulb,
  map: MapIcon,
  medal: Medal,
  "message-circle": MessageCircle,
  "message-square": MessageSquare,
  mountain: Mountain,
  "party-popper": PartyPopper,
  puzzle: Puzzle,
  rocket: Rocket,
  shield: Shield,
  "shield-check": ShieldCheck,
  sparkles: Sparkles,
  star: Star,
  swords: Swords,
  target: Target,
  trophy: Trophy,
  users: Users,
  zap: Zap,
};

const HEX = /^#[0-9a-f]{6}$/i;

export function BadgeIcon({ icon, color, size = 40, className }: { icon: string; color?: string | null; size?: number; className?: string }) {
  const Icon = ICONS[icon] ?? Award;
  const valid = color && HEX.test(color);
  return (
    <span
      aria-hidden
      className={cn("inline-flex shrink-0 items-center justify-center rounded-full ring-1 ring-border", !valid && "bg-accent-soft text-accent-strong", className)}
      style={{ width: size, height: size, ...(valid ? { background: `${color}26`, color } : {}) }}
    >
      <Icon style={{ width: size * 0.5, height: size * 0.5 }} />
    </span>
  );
}
