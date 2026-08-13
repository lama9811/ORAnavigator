// Relative time for stored-document rows.
//
// Both places that offer a PI back a solicitation they already uploaded — the
// upload modal's picker and Draft Review's panel — have to answer the same
// question: "is this the thing I was just doing?" A raw timestamp does not
// carry that; "20 minutes ago" does. Shared rather than duplicated so the two
// surfaces can never word the same fact differently.
//
// Timestamps arrive as naive UTC ISO strings from the backend
// (datetime.utcnow().isoformat(), no offset), so a bare `new Date()` would read
// them as LOCAL time and report a document uploaded minutes ago as hours old.
// The Z is appended when the string carries no zone of its own.
export function timeAgo(iso) {
  if (!iso) return "";
  const hasZone = iso.endsWith("Z") || /[+-]\d\d:?\d\d$/.test(iso);
  const then = new Date(hasZone ? iso : `${iso}Z`);
  const secs = Math.round((Date.now() - then.getTime()) / 1000);
  if (Number.isNaN(secs)) return "";
  if (secs < 0) return "just now";          // clock skew reads as the future
  if (secs < 90) return "just now";
  const mins = Math.round(secs / 60);
  if (mins < 60) return mins === 1 ? "a minute ago" : `${mins} minutes ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return hrs === 1 ? "an hour ago" : `${hrs} hours ago`;
  const days = Math.round(hrs / 24);
  return days === 1 ? "yesterday" : `${days} days ago`;
}
