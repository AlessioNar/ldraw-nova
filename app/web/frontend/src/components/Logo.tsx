/** The Open Brick Designer mark (a brick whose right half is dashed, "designed"). */
export default function Logo({ className = "brand-mark" }: { className?: string }) {
  return (
    <svg className={className} viewBox="3 7 26 19" fill="none" aria-hidden>
      <path fill="currentColor" d="M15.2 11.5H12.5v-2.3a.7.7 0 0 0-.7-.7H6.2a.7.7 0 0 0-.7.7v2.3H4.5A1.5 1.5 0 0 0 3 13v10.5A1.5 1.5 0 0 0 4.5 25h10.7z" />
      <path stroke="currentColor" strokeWidth="1.6" strokeDasharray="2.6 1.9" strokeLinecap="round" d="M17.6 12.3h2.7V9.3h5.4v3h1.8a.9.9 0 0 1 .9.9v10.1a.9.9 0 0 1-.9.9h-9.9" />
    </svg>
  );
}
