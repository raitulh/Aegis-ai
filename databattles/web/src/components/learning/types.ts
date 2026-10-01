/**
 * Response shapes for the learning endpoints (`backend/app/modules/learning/service.py` returns dicts).
 */
import type { CourseCard, Schemas, UserMini } from "@/lib/types";

export type LessonKind = "article" | "quiz" | "challenge";
export type LessonStatus = "not_started" | "in_progress" | "completed";
export type Difficulty = "beginner" | "intermediate" | "advanced";

export const DIFFICULTIES: Difficulty[] = ["beginner", "intermediate", "advanced"];
export const LESSON_KINDS: LessonKind[] = ["article", "quiz", "challenge"];
export const COVER_STYLES = ["aurora", "nebula", "circuit", "dunes", "mono", "sunrise"] as const;
/** Org roles that may author courses for their organization (see `Actor.is_org_manager`). */
export const MANAGER_ROLES = ["owner", "admin", "manager"];

export interface OutlineLesson {
  id: string;
  slug: string;
  title: string;
  kind: LessonKind;
  position: number;
  estimated_minutes: number;
  status: LessonStatus;
  quiz_score: number | null;
}

export interface CourseBadge {
  slug: string;
  name: string;
  icon: string;
  color: string;
  description: string;
}

export interface CourseDetail extends CourseCard {
  description_html: string | null;
  prerequisites: string[];
  author: UserMini | null;
  version: number;
  published_at: string | null;
  badge: CourseBadge | null;
  lessons: OutlineLesson[];
  resume_lesson_slug: string | null;
  certificate_public_id: string | null;
  can_author: boolean;
  outdated_enrollment: boolean;
}

export interface MyCourse extends CourseCard {
  enrolled_at: string;
  completed_at: string | null;
  resume_url: string;
}

export interface LearningPath {
  slug: string;
  title: string;
  summary: string | null;
  courses: { slug: string; title: string; difficulty: string; completed: boolean; estimated_minutes: number }[];
  completed_count: number;
  badge: { name: string; icon: string; color: string } | null;
}

export interface QuizQuestionView {
  id: string;
  prompt: string;
  options: string[];
  /** Present only once the learner has passed the quiz. */
  correct_option?: number;
  explanation?: string | null;
}

export interface LessonRef {
  slug: string;
  title: string;
}

export interface LessonDetail {
  course: { slug: string; title: string };
  id: string;
  slug: string;
  title: string;
  kind: LessonKind;
  body_html: string | null;
  estimated_minutes: number;
  pass_threshold: number;
  questions: QuizQuestionView[];
  challenge: { type: string; instructions: string; competition: { slug: string; title: string } | null } | null;
  progress: { status: LessonStatus; quiz_score: number | null; attempts: number };
  enrolled: boolean;
  prev: LessonRef | null;
  next: LessonRef | null;
  position: number;
  total: number;
}

export interface ProgressResult {
  course_progress: number;
  course_completed: boolean;
}

export interface CompleteResult extends ProgressResult {
  status: LessonStatus;
}

export interface QuizQuestionResult {
  question_id: string;
  correct: boolean;
  chosen: number | null;
  correct_option?: number;
  explanation?: string | null;
}

export interface QuizResult extends ProgressResult {
  score: number;
  passed: boolean;
  pass_threshold: number;
  correct: number;
  total: number;
  attempts: number;
  results: QuizQuestionResult[];
}

export interface ChallengeResult extends ProgressResult {
  status: LessonStatus;
  passed: boolean;
  competition: { slug: string; title: string };
  message: string;
}

export interface AuthoringQuestion {
  id: string;
  prompt: string;
  options: string[];
  correct_option: number;
  explanation: string | null;
}

export interface ChallengeConfig {
  type?: string;
  competition_slug: string;
  instructions: string;
  min_score: number | null;
}

export interface AuthoringLesson {
  id: string;
  slug: string;
  title: string;
  kind: LessonKind;
  position: number;
  body_md: string | null;
  estimated_minutes: number;
  pass_threshold: number;
  challenge: ChallengeConfig | null;
  questions: AuthoringQuestion[];
}

export interface AuthoringCourse extends CourseCard {
  description_md: string | null;
  prerequisites: string[];
  version: number;
  badge_slug: string | null;
  org_id: string | null;
  stats: { enrolled: number; completed: number };
  lessons: AuthoringLesson[];
}

export type OrgMembership = Schemas["app__modules__orgs__router__MembershipOut"];
export type Badge = Schemas["BadgeOut"];
