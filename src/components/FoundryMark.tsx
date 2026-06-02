export function FoundryMark({ size = 20 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 512 512" fill="none" aria-hidden="true">
      <rect width="512" height="512" rx="112" fill="#1e1d1a" />
      <path
        d="M116 194v-56c0-13 10-23 23-23h57M316 115h57c13 0 23 10 23 23v57M396 316v57c0 13-10 23-23 23h-57M196 396h-57c-13 0-23-10-23-23v-57"
        fill="none"
        stroke="#f6f5f2"
        strokeWidth="23"
        strokeLinecap="round"
      />
      <rect x="143" y="145" width="226" height="226" rx="44" fill="#f6f5f2" />
      <path d="M181 184h152v47h-99v51h82v42h-82v84h-53V184Z" fill="#1e1d1a" />
      <path
        d="M331 121h43c12 0 22 10 22 22v43"
        fill="none"
        stroke="#d97845"
        strokeWidth="19"
        strokeLinecap="round"
      />
      <path
        d="M351 225v82c0 26-21 47-47 47h-70"
        fill="none"
        stroke="#d97845"
        strokeWidth="15"
        strokeLinecap="round"
      />
      <path d="M351 219l25 25-25 25-25-25 25-25Z" fill="#d97845" />
    </svg>
  );
}
