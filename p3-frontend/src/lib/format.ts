// C0: the API sends UTC ISO-8601; the UI shows the viewer's local time.

// en-GB gives "Tue 13 Oct, 23:40" (24-hour). The time zone is the viewer's own (C0).
const LOCALE = "en-GB";

const dayTime = new Intl.DateTimeFormat(LOCALE, {
  weekday: "short",
  day: "numeric",
  month: "short",
  hour: "2-digit",
  minute: "2-digit",
});
const fullDate = new Intl.DateTimeFormat(LOCALE, {
  weekday: "short",
  day: "numeric",
  month: "short",
  year: "numeric",
  hour: "2-digit",
  minute: "2-digit",
});
const timeOnly = new Intl.DateTimeFormat(LOCALE, { hour: "2-digit", minute: "2-digit" });
const timeSeconds = new Intl.DateTimeFormat(LOCALE, { hour: "2-digit", minute: "2-digit", second: "2-digit" });
const weekdayTime = new Intl.DateTimeFormat(LOCALE, { weekday: "short", hour: "2-digit", minute: "2-digit" });

function toDate(value: string | number | Date): Date {
  return value instanceof Date ? value : new Date(value);
}

/** "Tue 13 Oct, 23:40" in local time */
export const formatDayTime = (v: string | number | Date) => dayTime.format(toDate(v));
/** "Tue 13 Oct 2026, 23:40" */
export const formatFull = (v: string | number | Date) => fullDate.format(toDate(v));
/** "23:40" */
export const formatTime = (v: string | number | Date) => timeOnly.format(toDate(v));
/** "23:40:02" */
export const formatTimeSeconds = (v: string | number | Date) => timeSeconds.format(toDate(v));
/** "Wed 23:40" */
export const formatWeekdayTime = (v: string | number | Date) => weekdayTime.format(toDate(v));

/** C0: probabilities are 0..1; the UI shows percentages. */
export const percent = (p: number | null | undefined) =>
  p === null || p === undefined ? "—" : `${Math.round(p * 100)}%`;

export function money(m: { amount: number; currency: string } | null | undefined): string {
  if (!m) return "—";
  return new Intl.NumberFormat("en-GB", {
    style: "currency",
    currency: m.currency || "EUR",
    maximumFractionDigits: 0,
  }).format(m.amount);
}

export const count = (n: number | null | undefined) =>
  n === null || n === undefined ? "—" : new Intl.NumberFormat("en-GB").format(n);

export function hoursText(h: number | null | undefined): string {
  if (h === null || h === undefined) return "—";
  if (h < 1) return `${Math.round(h * 60)} min`;
  return `about ${Math.round(h)} h`;
}

export function durationText(h: number): string {
  if (h < 1) return `${Math.round(h * 60)} minutes`;
  const rounded = Math.round(h * 10) / 10;
  return `${rounded} ${rounded === 1 ? "hour" : "hours"}`;
}

/** "about 6 minutes", "about 3 hours" — used for "time to make a plan" */
export function elapsedText(fromIso: string, toIso: string): string {
  const mins = Math.max(0, (new Date(toIso).getTime() - new Date(fromIso).getTime()) / 60000);
  if (mins < 90) return `about ${Math.max(1, Math.round(mins))} minutes`;
  return `about ${Math.round(mins / 60)} hours`;
}

export const initials = (name: string) =>
  name
    .split(/[\s._-]+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((w) => w[0]!.toUpperCase())
    .join("") || "?";
