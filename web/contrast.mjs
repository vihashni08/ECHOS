// WCAG 2.x contrast check of the design tokens (src/index.css values). node contrast.mjs
const lum = (h) => { const v = [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16) / 255).map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4)); return 0.2126 * v[0] + 0.7152 * v[1] + 0.0722 * v[2] }
const cr = (a, b) => { const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p); return (x + 0.05) / (y + 0.05) }
const T = {
  light: { background: "#f5f8fb", foreground: "#172029", card: "#ffffff", primary: "#0b5a5e", "primary-foreground": "#ffffff", muted: "#eaf0f5", "muted-foreground": "#4f5b68", accent: "#e3efee", "accent-foreground": "#0b3f42", destructive: "#b42318",
    "normal-bg": "#e3f4e6", "normal-fg": "#14532d", "monitor-bg": "#fdf0cc", "monitor-fg": "#6b3a05", "urgent-bg": "#fde2e0", "urgent-fg": "#8a1710", "gap-bg": "#e8e5e0", "gap-fg": "#3a352f" },
  dark: { background: "#12100e", foreground: "#f2ede6", card: "#1b1815", primary: "#5fc4c0", "primary-foreground": "#06201f", muted: "#221e1b", "muted-foreground": "#b3a99d", accent: "#17302f", "accent-foreground": "#bfeeea", destructive: "#f87171",
    "normal-bg": "#12301d", "normal-fg": "#b8f0c6", "monitor-bg": "#3a2a08", "monitor-fg": "#f6d98a", "urgent-bg": "#471513", "urgent-fg": "#fecaca", "gap-bg": "#2d2925", "gap-fg": "#d6cfc6" },
}
const pairs = [["foreground", "background"], ["foreground", "card"], ["muted-foreground", "background"], ["muted-foreground", "card"], ["muted-foreground", "muted"], ["primary-foreground", "primary"], ["primary", "background"], ["primary", "card"], ["accent-foreground", "accent"],
  ["normal-fg", "normal-bg"], ["monitor-fg", "monitor-bg"], ["urgent-fg", "urgent-bg"], ["gap-fg", "gap-bg"], ["normal-fg", "card"], ["urgent-fg", "card"], ["destructive", "card"]]
let bad = 0
for (const [mode, t] of Object.entries(T)) for (const [f, b] of pairs) { const r = cr(t[f], t[b]); const ok = r >= 4.5; if (!ok) bad++; console.log(`${mode.padEnd(5)} ${f.padEnd(18)} on ${b.padEnd(11)} ${r.toFixed(2)} ${ok ? "AA" : "FAIL"}`) }
process.exit(bad ? 1 : 0)
