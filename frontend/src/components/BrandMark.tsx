export function BrandMark({ className }: { className?: string }) {
  return (
    <svg
      aria-hidden="true"
      className={className}
      focusable="false"
      viewBox="0 0 64 64"
    >
      <path
        d="M14 30 L32 14 L50 30"
        fill="none"
        stroke="#cfb46c"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth="5"
      />
      <g fill="none" stroke="currentColor" strokeWidth="3">
        <rect height="16" rx="2" width="28" x="18" y="36" />
        <path d="M18 44 H46" />
        <path d="M32 36 V52" />
      </g>
    </svg>
  );
}
