# Light Theme Conversion TODO

Every HTML file in `static/` uses a dark color scheme. This document lists each file, its dark-theme CSS patterns, and the changes needed to convert to a white/light theme.

---

## Shared CSS: `static/sidebar.css`

Used by most pages. All sidebar variables are dark.

| Current Variable | Dark Value | Light Value |
|---|---|---|
| `--sb-bg` | `#141416` | `#F5F5F7` |
| `--sb-border` | `rgba(255,255,255,0.06)` | `rgba(0,0,0,0.06)` |
| `--sb-text` | `rgba(245,245,247,0.5)` | `rgba(28,28,30,0.5)` |
| `--sb-text-hover` | `rgba(245,245,247,0.85)` | `rgba(28,28,30,0.85)` |
| `--sb-hover-bg` | `rgba(255,255,255,0.03)` | `rgba(0,0,0,0.03)` |

Additional sidebar hardcoded colors to change:
- `.rn-sb-logo-text` color `#F5F5F7` → `#1C1C1E`
- `.rn-sb-logo-sub` color `rgba(245,245,247,0.25)` → `rgba(28,28,30,0.25)`
- `.rn-sb-parent` color `rgba(245,245,247,0.7)` → `rgba(28,28,30,0.7)`
- `.rn-sb-group.open .rn-sb-parent` color `#F5F5F7` → `#1C1C1E`
- `.rn-sb-location` background `rgba(255,255,255,0.04)` → `rgba(0,0,0,0.04)`, color `#F5F5F7` → `#1C1C1E`
- `.rn-sb-location option` background `#1C1C1E` → `#FFFFFF`, color `#F5F5F7` → `#1C1C1E`
- `.rn-main` background `#1C1C1E` → `#FFFFFF`
- `.rn-hamburger` background `rgba(28,28,30,0.9)` → `rgba(245,245,247,0.9)`
- `.rn-topbar` background `rgba(28,28,30,0.85)` → `rgba(245,245,247,0.85)`
- `.me-rv-*` classes: all `#f5f5f7` text → `#1C1C1E`, all `rgba(255,255,255,X)` → `rgba(0,0,0,X)`, `#2a2a2c`/`#3a3a3c` backgrounds → `#E5E5E7`/`#D5D5D7`

---

## Pattern Groups

Files share one of these dark-theme patterns:

### Pattern A: Classic Dark (`#1C1C1E`)
Files: `catalog.html`, `index.html`, `ai_inventory.html`, `local_upload.html`, `live_record.html`

| Variable | Dark | Light |
|---|---|---|
| `--bg` | `#1C1C1E` | `#FFFFFF` |
| `--bg-elevated` | `rgba(44,44,46,0.98)` | `#F5F5F7` |
| `--bg-card` | `rgba(58,58,60,0.5)` | `rgba(0,0,0,0.03)` |
| `--bg-glass` | `rgba(58,58,60,0.4)` | `rgba(0,0,0,0.02)` |
| `--text` | `#F5F5F7` | `#1C1C1E` |
| `--text2` | `rgba(245,245,247,0.65)` | `rgba(28,28,30,0.6)` |
| `--text3` | `rgba(245,245,247,0.4)` | `rgba(28,28,30,0.4)` |
| `--border` | `rgba(255,255,255,0.1)` | `rgba(0,0,0,0.08)` |
| meta theme-color | `#1C1C1E` | `#FFFFFF` |

### Pattern B: Very Dark (`#0a0a0c`)
Files: `count.html`, `storage.html`

| Variable | Dark | Light |
|---|---|---|
| `--bg` | `#0a0a0c` | `#FFFFFF` |
| `--card` | `#1a1a1e` | `#F9F9FB` |
| `--card2` | `#222226` | `#F0F0F3` |
| `--text` | `#f5f5f7` | `#0a0a0c` |
| `--text2` | `rgba(245,245,247,0.6)` | `rgba(10,10,12,0.6)` |
| `--text3` | `rgba(245,245,247,0.35)` | `rgba(10,10,12,0.35)` |
| `--border` | `rgba(255,255,255,0.08)` | `rgba(0,0,0,0.08)` |
| `--border2` | `rgba(255,255,255,0.15)` | `rgba(0,0,0,0.15)` |
| header bg | `rgba(10,10,12,0.85-0.92)` | `rgba(245,245,247,0.85-0.92)` |

### Pattern C: Slate Dark (`#020617`)
Files: `order_guide.html`, `specials_admin.html`, `vendor_status.html`, `payments.html`, `voice_recipe.html`, `product_mapping.html`

| Variable | Dark | Light |
|---|---|---|
| `--bg` | `#020617` | `#FFFFFF` |
| `--bg-card` | `#0f172a` | `#F8FAFC` |
| `--bg-elevated` | `#1e293b` | `#F1F5F9` |
| `--border` | `#1e293b` | `#E2E8F0` |
| `--border-focus` | `#334155` | `#CBD5E1` |
| `--text` | `#e2e8f0` | `#0F172A` |
| `--text2` | `#94a3b8` | `#475569` |
| `--text3` | `#475569` | `#94A3B8` |
| modal overlay | `rgba(2,6,23,0.85)` | `rgba(0,0,0,0.3)` |

### Pattern D: Management Dark (`#0F0F11`)
Files: `manage.html`, `plan.html`

| Variable | Dark | Light |
|---|---|---|
| `--bg` | `#0F0F11` | `#FFFFFF` |
| `--sidebar-bg` | `#18181B` | `#F5F5F7` |
| `--card-bg` | `#1E1E21` | `#F9F9FB` |
| `--text` | `#F5F5F7` | `#0F0F11` |
| `--text2` | `rgba(245,245,247,0.6)` | `rgba(15,15,17,0.6)` |
| `--text3` | `rgba(245,245,247,0.35)` | `rgba(15,15,17,0.35)` |
| `--border` | `rgba(255,255,255,0.08)` | `rgba(0,0,0,0.08)` |
| `--border2` | `rgba(255,255,255,0.15)` | `rgba(0,0,0,0.15)` |

### Pattern E: Chalkboard (decorative)
File: `chalkboard_specials_portrait.html`

| Element | Dark | Light |
|---|---|---|
| body background | `#1a1a1a` | `#FFFFFF` or `#FEFEF9` |
| board gradient | `linear-gradient(160deg, #1b3a2e, #0f2318, #0a1c10)` | `linear-gradient(160deg, #E8F5F0, #F8FCFA, #F5F9F8)` |
| chalk text | `rgba(240,235,210,X)` | `rgba(20,20,20,X)` |
| chalk borders | `rgba(240,235,210,0.25)` | `rgba(100,100,100,0.15)` |

---

## Per-File Checklist

- [ ] `static/sidebar.css` — shared sidebar (see table above)
- [ ] `static/catalog.html` — Pattern A
- [ ] `static/index.html` — Pattern A + header `rgba(28,28,30,0.85)`, sidebar `rgba(24,24,27,0.98)`
- [ ] `static/ai_inventory.html` — Pattern A + page tabs `rgba(28,28,30,0.92)`
- [ ] `static/local_upload.html` — Pattern A + upload zone `rgba(44,44,46,0.5)` → `rgba(0,0,0,0.02)`
- [ ] `static/live_record.html` — Pattern A (accent colors like red record button are fine)
- [ ] `static/count.html` — Pattern B + section header `rgba(10,10,12,0.92)`
- [ ] `static/storage.html` — Pattern B + header `rgba(10,10,12,0.9)`
- [ ] `static/order_guide.html` — Pattern C
- [ ] `static/specials_admin.html` — Pattern C + modal overlay
- [ ] `static/vendor_status.html` — Pattern C
- [ ] `static/payments.html` — Pattern C + table striping `rgba(2,6,23,0.5)` → `rgba(0,0,0,0.02)`, modal overlay
- [ ] `static/voice_recipe.html` — Pattern C (accent button gradients are fine on light)
- [ ] `static/product_mapping.html` — Pattern C
- [ ] `static/manage.html` — Pattern D
- [ ] `static/plan.html` — Pattern D
- [ ] `static/chalkboard_specials_portrait.html` — Pattern E (full decorative rework)
- [ ] `static/invoices.html` — Likely Pattern A or B (large file, verify before converting)

## Notes

- **Accent colors** (red `#FF453A`, green `#30D158`, blue `#0A84FF`) work well on light backgrounds — no changes needed.
- **Backdrop blur** values stay the same; they adapt naturally.
- **`<meta name="theme-color">`** tags in each file need updating to `#FFFFFF`.
- **SVG icons** with hardcoded light strokes/fills may need darkening.
- **Box shadows** using `rgba(0,0,0,X)` already work on light; glow effects with light rgba may need adjustment.
