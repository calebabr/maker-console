import { formatTimeParts, parseDate } from '../utils/format.js';

/**
 * A time in the device's time zone ("9:41 PM"); hover / long-press shows the exact UTC instant.
 * `value`: ISO string, epoch ms or Date. Use this instead of toLocale*String in pages.
 */
export default function Time({ value, withDate = false, className }) {
  const d = parseDate(value);
  const { text, title } = formatTimeParts(value, { withDate });
  if (!d) return <span className={className}>{text}</span>;
  return (
    <time className={className} dateTime={d.toISOString()} title={title}>
      {text}
    </time>
  );
}
