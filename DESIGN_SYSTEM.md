# Geist Design System — AI Agent Reference & Prompt Guide

> **Purpose**: This document is a complete, self-contained design system specification. Use it as an instruction prompt for AI coding agents (Claude, GPT, Gemini, Cursor, etc.) when building any landing page, dashboard, or web application that must follow the Vercel/Geist visual language.
>
> **How to use**: Copy this entire file (or relevant sections) into the system prompt or context window of any AI agent. It contains every rule, token, class, pattern, and code snippet needed to produce pixel-accurate Geist-styled pages without referencing external files.

---

## Table of Contents

1. [Philosophy & Aesthetic Principles](#1-philosophy--aesthetic-principles)
2. [Tech Stack & Setup](#2-tech-stack--setup)
3. [Color System](#3-color-system)
4. [Typography](#4-typography)
5. [Spacing & Layout](#5-spacing--layout)
6. [Border & Radius](#6-border--radius)
7. [Shadows & Elevation](#7-shadows--elevation)
8. [Dark Mode Implementation](#8-dark-mode-implementation)
9. [Animation & Transitions](#9-animation--transitions)
10. [Icon System](#10-icon-system)
11. [Component Catalog](#11-component-catalog)
    - [Buttons](#buttons)
    - [Inputs & Form Elements](#inputs--form-elements)
    - [Badges & Pills](#badges--pills)
    - [Status Indicators](#status-indicators)
    - [Cards](#cards)
    - [Navigation](#navigation)
    - [Tables & Data Display](#tables--data-display)
    - [Accordion / Expandable Sections](#accordion--expandable-sections)
    - [Search Components](#search-components)
    - [Toggle Groups](#toggle-groups)
    - [Alert / Info Banners](#alert--info-banners)
    - [Empty States](#empty-states)
    - [Promo / CTA Cards](#promo--cta-cards)
12. [Responsive Rules](#12-responsive-rules)
13. [Interaction States Reference Table](#13-interaction-states-reference-table)
14. [Do's and Don'ts](#14-dos-and-donts)
15. [Full CSS Foundation (Copy-Paste Ready)](#15-full-css-foundation)
16. [Quick-Start Prompt Template](#16-quick-start-prompt-template)

---

## 1. Philosophy & Aesthetic Principles

```
AESTHETIC = Ultra-minimalist + Developer-centric + Monochromatic
```

### Core Rules

| Rule | Description |
|------|-------------|
| **Minimalism over decoration** | Rely on whitespace, borders, and typography contrast — NOT gradients, heavy shadows, or vivid colors on containers. |
| **Monochromatic palette** | The entire UI is black, white, and grays. Color is reserved ONLY for: status indicators, accent links (`#0070f3` blue), and error states (`#e00` red). |
| **Typography hierarchy** | Distinguish content levels through font-weight and size, not color variety. Primary text is near-black/near-white. Secondary text is mid-gray. |
| **Borders define structure** | Cards, containers, and sections are separated by 1px borders — never by drop shadows or background color changes. |
| **Density** | The UI is information-dense. Use `text-sm` (14px) as the default body text, `text-xs` (12px) for metadata/labels, and reserve `text-lg`+ only for page-level headings. |
| **Rounded, never sharp** | ALL interactive elements and containers use `rounded-md` (6px) or `rounded-lg` (8px). Never use sharp corners (`rounded-none`) on primary UI elements. |
| **Subtle, purposeful animation** | Transitions should be functional (hover feedback, expand/collapse), never decorative. Duration: 150ms–300ms. Easing: `ease-out`. |

---

## 2. Tech Stack & Setup

### Required Dependencies

```json
{
  "dependencies": {
    "next": "^16.x",
    "react": "^19.x",
    "react-dom": "^19.x",
    "lucide-react": "^0.x"
  },
  "devDependencies": {
    "tailwindcss": "^4.x",
    "@tailwindcss/postcss": "^4.x",
    "typescript": "^5.x"
  }
}
```

### Font Setup (layout.tsx)

```tsx
import { Geist, Geist_Mono } from "next/font/google";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

// Apply to <html>:
// className={`${geistSans.variable} ${geistMono.variable} antialiased`}
```

### Dark Mode Setup (Tailwind v4)

```css
@import "tailwindcss";
@custom-variant dark (&:where(.dark, .dark *));
```

Toggle via JavaScript:
```tsx
document.documentElement.classList.toggle("dark");
```

---

## 3. Color System

### Light Mode Palette

| Token | Hex | Tailwind Class | Usage |
|-------|-----|----------------|-------|
| Background (primary) | `#ffffff` | `bg-white` | Page background |
| Background (secondary) | `#fafafa` | `bg-gray-50` | Sidebar, subtle surfaces |
| Foreground (primary) | `#0a0a0a` | `text-gray-900` | Headings, primary text |
| Foreground (secondary) | `#666666` | `text-gray-500` | Secondary text, labels |
| Foreground (tertiary) | `#888888` | `text-gray-400` | Placeholder text, icons |
| Border (default) | `#eaeaea` | `border-gray-200` | All container borders |
| Border (hover) | `#999999` | `border-gray-400` | Border on hover |
| Highlight / Hover BG | `#f5f5f5` | `bg-gray-50` | Row hover, button hover |
| Card surface | `#ffffff` | `bg-white` | Card background |
| Accent | `#0070f3` | `text-blue-500` | Links, active indicators |
| Error | `#ee0000` | `text-red-500` | Error states, danger |
| Warning | `#f5a623` | `text-yellow-500` | Warning indicators |

### Dark Mode Palette

| Token | Hex | Tailwind Class | Usage |
|-------|-----|----------------|-------|
| Background (primary) | `#000000` | `bg-black` | Page background |
| Background (secondary) | `#0a0a0a` | `bg-[#0a0a0a]` | Cards, sidebar |
| Foreground (primary) | `#ededed` | `text-gray-100` | Headings, primary text |
| Foreground (secondary) | `#888888` | `text-gray-400` | Secondary text |
| Foreground (tertiary) | `#666666` | `text-gray-500` | Disabled, placeholder |
| Border (default) | `rgba(255,255,255,0.1)` | `border-white/10` | All container borders |
| Border (hover) | `rgba(255,255,255,0.2)` | `border-white/20` | Border on hover |
| Highlight / Hover BG | `#111111` | `bg-gray-900` | Row hover, button hover |
| Card surface | `#0a0a0a` | `bg-[#0a0a0a]` | Card background |

### The Color Inversion Rule

```
CRITICAL RULE: In dark mode, "Primary" buttons INVERT.
  Light: bg-gray-900 text-white  →  Dark: bg-white text-black
  Light: bg-white text-gray-900  →  Dark: bg-black text-gray-100
```

---

## 4. Typography

### Font Stack

```css
font-family: var(--font-geist-sans), -apple-system, BlinkMacSystemFont,
             'Segoe UI', Roboto, sans-serif;
```

Monospace (for code, data values):
```css
font-family: var(--font-geist-mono), 'SF Mono', Monaco, monospace;
```

### Type Scale

| Level | Tailwind | Size | Weight | Use Case |
|-------|----------|------|--------|----------|
| Page Title | `text-2xl font-bold` | 24px | 700 | Page-level headings only |
| Section Heading | `text-lg font-semibold` | 18px | 600 | Section titles (e.g., "Networking", "Usage") |
| Card Title | `text-sm font-semibold` | 14px | 600 | Card headers, nav items |
| Body Text | `text-sm` | 14px | 400 | Default text, descriptions |
| Metadata / Label | `text-xs` | 12px | 400–500 | Dates, URLs, counts, badges |
| Tiny Label | `text-[10px]` | 10px | 400 | Keyboard shortcut hints |
| Data Value | `text-sm font-mono` | 14px | 400 | Numbers, usage stats, monospace data |
| Uppercase Label | `text-xs font-medium uppercase tracking-wider` | 12px | 500 | Form field labels |

### Typography Rules

1. **NEVER use `text-base` (16px) as default** — always use `text-sm` (14px).
2. **NEVER use colored text** for content — only use gray tones. Color is reserved for links (`text-blue-500`) and status.
3. **Use `font-mono` for data** — all numbers, file sizes, dates in data contexts.
4. **Use `truncate`** on any text that might overflow (project names, URLs, commit messages).
5. **Anti-aliasing is mandatory**: `-webkit-font-smoothing: antialiased` on `<html>`.

---

## 5. Spacing & Layout

### Spacing Scale

| Token | Value | Tailwind | Usage |
|-------|-------|----------|-------|
| 4xs | 2px | `gap-0.5`, `p-0.5` | Between inline badge elements |
| 3xs | 4px | `gap-1`, `p-1` | Icon button padding |
| 2xs | 6px | `gap-1.5`, `p-1.5` | Nav item padding, icon gaps |
| xs | 8px | `gap-2`, `p-2` | Button padding, small gaps |
| sm | 12px | `gap-3`, `p-3` | Card padding, nav padding |
| md | 16px | `gap-4`, `p-4` | Standard card inner padding |
| lg | 24px | `gap-6`, `p-6` | Section gaps, page padding |
| xl | 32px | `gap-8`, `p-8` | Major section separations |

### Layout Patterns

```
SIDEBAR LAYOUT:
┌──────────┬───────────────────────────────────┐
│ Sidebar  │  Header (h-12)                    │
│ w-[220px]│───────────────────────────────────│
│          │  Tab Navigation                   │
│          │───────────────────────────────────│
│          │  Content (flex-1, overflow-y-auto)│
│          │                                   │
│          │  ┌─────────┬─────────────────┐   │
│          │  │ Left    │  Right Column   │   │
│          │  │ w-[320] │  flex-1         │   │
│          │  │         │  grid-cols-2    │   │
│          │  └─────────┴─────────────────┘   │
└──────────┴───────────────────────────────────┘
```

```tsx
// Root layout structure
<div className="flex h-screen">
  <aside className="w-[220px] shrink-0 border-r border-gray-200 dark:border-white/10">
    {/* Sidebar */}
  </aside>
  <div className="flex-1 flex flex-col min-w-0">
    <header className="h-12 border-b shrink-0">{/* Header */}</header>
    <main className="flex-1 overflow-hidden">
      <div className="p-6 max-w-[1200px] mx-auto">{/* Content */}</div>
    </main>
  </div>
</div>
```

### Content Max-Width

- Dashboard content: `max-w-[1200px] mx-auto`
- Form / showcase content: `max-w-[900px] mx-auto`
- Full-width tables: no max-width, use parent constraints

---

## 6. Border & Radius

### Border Rules

| Element | Light | Dark |
|---------|-------|------|
| Container/Card border | `border border-gray-200` | `dark:border-white/10` |
| Divider line | `border-b border-gray-200` | `dark:border-white/10` |
| Subtle table row divider | `border-b border-gray-100` | `dark:border-white/5` |
| Input border | `border border-gray-200` | `dark:border-white/10` |
| Error input border | `border border-red-300` | `dark:border-red-800` |

### Border Radius Values

| Element | Class | Pixels |
|---------|-------|--------|
| Buttons, inputs, cards, containers | `rounded-lg` | 8px |
| Small badges, tags | `rounded-md` | 6px |
| Pill badges | `rounded-full` | 9999px |
| Avatars, status dots | `rounded-full` | 9999px |
| Never use on main elements | `rounded-none` | 0px ❌ |

---

## 7. Shadows & Elevation

```
RULE: This design system uses ZERO box-shadows for structural elements.
Elevation is communicated through BORDERS, not shadows.
```

The only shadow used is the animated `pulse-glow` on deployment status indicators:
```css
box-shadow: 0 0 0 4px rgba(0, 112, 243, 0.4); /* keyframed, not static */
```

---

## 8. Dark Mode Implementation

### Toggle Mechanism

```tsx
// State management
const [isDark, setIsDark] = useState(false);

const toggleDark = () => {
  setIsDark(!isDark);
  document.documentElement.classList.toggle("dark");
};
```

### Class Pattern

Every visual element MUST have both light and dark variants:

```tsx
// ✅ CORRECT — always pair light + dark
className="bg-white dark:bg-[#0a0a0a] border-gray-200 dark:border-white/10 text-gray-900 dark:text-white"

// ❌ WRONG — missing dark variant
className="bg-white border-gray-200 text-gray-900"
```

### Common Light → Dark Mappings

```
bg-white              → dark:bg-black          (page bg)
bg-white              → dark:bg-[#0a0a0a]      (cards, containers)
bg-gray-50            → dark:bg-gray-900       (hover states)
bg-gray-100           → dark:bg-gray-800       (active states, badges)
border-gray-200       → dark:border-white/10   (all borders)
text-gray-900         → dark:text-white        (primary text)
text-gray-700         → dark:text-gray-300     (body text)
text-gray-600         → dark:text-gray-400     (secondary text)
text-gray-500         → dark:text-gray-500     (tertiary — SAME in both)
text-gray-400         → dark:text-gray-500     (placeholder)
hover:bg-gray-50      → dark:hover:bg-gray-900 (hover backgrounds)
hover:bg-gray-100     → dark:hover:bg-gray-800 (active hover)
```

---

## 9. Animation & Transitions

### Transition Defaults

ALL interactive elements must have `transition-colors` or `transition-all`:
```tsx
className="... transition-colors"       // For color-only changes
className="... transition-all duration-200" // For size + color changes
className="... transition-all duration-150" // For nav items
```

### Defined Animations

#### 1. Fade In (Cards, content appearing)
```css
@keyframes fadeIn {
  from { opacity: 0; transform: translateY(4px); }
  to   { opacity: 1; transform: translateY(0); }
}
.animate-fade-in {
  animation: fadeIn 0.3s ease-out forwards;
}
```

Usage: Apply to cards with stagger:
```tsx
className={`animate-fade-in opacity-0 stagger-${index}`}
```

#### 2. Stagger Delays (Card grid entrance)
```css
.stagger-1 { animation-delay: 0.05s; }
.stagger-2 { animation-delay: 0.1s; }
.stagger-3 { animation-delay: 0.15s; }
.stagger-4 { animation-delay: 0.2s; }
.stagger-5 { animation-delay: 0.25s; }
.stagger-6 { animation-delay: 0.3s; }
```

#### 3. Accordion Expand/Collapse
```css
@keyframes accordion-open {
  from { max-height: 0; opacity: 0; }
  to   { max-height: 500px; opacity: 1; }
}
.accordion-content-open {
  animation: accordion-open 0.3s ease-out forwards;
  overflow: hidden;
}
```

#### 4. Pulse Glow (Status indicators)
```css
@keyframes pulse-glow {
  0%, 100% { box-shadow: 0 0 0 0 rgba(0, 112, 243, 0.4); }
  50%      { box-shadow: 0 0 0 4px rgba(0, 112, 243, 0); }
}
.pulse-glow { animation: pulse-glow 2s infinite; }
```

#### 5. Progress Ring (SVG circular progress)
```css
@keyframes progress-ring {
  0%   { stroke-dashoffset: 100; }
  100% { stroke-dashoffset: var(--progress-offset, 0); }
}
.progress-ring-circle {
  animation: progress-ring 1s ease-out forwards;
  transform: rotate(-90deg);
  transform-origin: 50% 50%;
}
```

### Animation Rules

1. **Duration**: 150ms for hovers, 200–300ms for enters/exits, 2s for looping indicators.
2. **Easing**: `ease-out` for entrances, `ease-in` for exits. Never `linear` on UI elements.
3. **Hover reveal pattern**: Elements that appear on hover use `opacity-0 group-hover:opacity-100 transition-opacity`.
4. **Never animate layout shifts** — use `opacity` and `transform` only.

---

## 10. Icon System

### Library: `lucide-react`

```tsx
import { Search, ChevronDown, MoreHorizontal, ... } from "lucide-react";
```

### Icon Size Rules

| Context | Size Class | Pixels | Example |
|---------|------------|--------|---------|
| Navigation items | `w-4 h-4` | 16px | Sidebar nav icons |
| Inline with text | `w-3.5 h-3.5` | 14px | Chevrons, small indicators |
| Tiny metadata | `w-3 h-3` | 12px | Git branch icon, expand arrows |
| Empty state | `w-5 h-5` | 20px | Centered in circle container |
| Icon-only button | `w-4 h-4` | 16px | Inside `p-2` button |

### Icon Color Rules

```
Default icon color:     text-gray-400 (light) / text-gray-500 (dark)
Hover icon color:       text-gray-600 (light) / text-gray-300 (dark)
Active/selected icon:   text-gray-900 (light) / text-white (dark)
```

### Standard Icons by Context

```tsx
// Navigation
FolderKanban   → Projects
Rocket         → Deployments
ScrollText     → Logs
BarChart3      → Analytics
Zap            → Speed Insights
Eye            → Observability
Shield         → Firewall
Globe2         → CDN
Variable       → Environment Variables
Globe          → Domains
Link2          → Connect
Puzzle         → Integrations
Database       → Storage
Flag           → Flags

// Actions
Search         → Search inputs
Plus           → Add/Create
X              → Close/Dismiss
MoreHorizontal → Options menu (ellipsis)
ChevronDown    → Dropdown, accordion
ChevronRight   → Navigate forward, expandable
SlidersHorizontal → Filter/Settings

// Data/Status
Check          → Success/Completed
AlertCircle    → Error state
Info           → Info banner
GitBranch      → Git branch indicator
ExternalLink   → External link
CalendarDays   → Date picker
Maximize2      → Expand/Fullscreen

// Theme
Sun            → Light mode indicator
Moon           → Dark mode indicator

// Layout
LayoutGrid     → Grid view
List           → List view
ArrowLeft      → Back navigation
```

### Icon Always With `shrink-0`

```tsx
// ✅ ALWAYS prevent icon squishing in flex containers
<Icon className="w-4 h-4 shrink-0" />
```

---

## 11. Component Catalog

### Buttons

#### Primary Button
```tsx
<button className="px-4 py-2 text-sm font-medium rounded-lg bg-gray-900 dark:bg-white text-white dark:text-black hover:bg-gray-800 dark:hover:bg-gray-100 transition-colors">
  Primary
</button>
```

#### Secondary / Outline Button
```tsx
<button className="px-4 py-2 text-sm font-medium rounded-lg border border-gray-200 dark:border-white/20 text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-800 transition-colors">
  Secondary
</button>
```

#### Danger Button
```tsx
<button className="px-4 py-2 text-sm font-medium rounded-lg bg-red-600 text-white hover:bg-red-700 transition-colors">
  Danger
</button>
```

#### Ghost / Text Button
```tsx
<button className="px-4 py-2 text-sm font-medium rounded-lg text-blue-600 dark:text-blue-400 hover:bg-blue-50 dark:hover:bg-blue-950 transition-colors">
  Ghost
</button>
```

#### Icon-Only Button
```tsx
<button className="p-2 rounded-lg border border-gray-200 dark:border-white/20 text-gray-500 hover:bg-gray-50 dark:hover:bg-gray-800 transition-colors">
  <Plus className="w-4 h-4" />
</button>
```

#### Small Primary Button (for cards, inline)
```tsx
<button className="text-xs font-medium px-3 py-1 rounded-md bg-gray-900 dark:bg-white text-white dark:text-black hover:bg-gray-800 dark:hover:bg-gray-100 transition-colors">
  Upgrade
</button>
```

#### Primary Button with Chevron (Dropdown Trigger)
```tsx
<button className="flex items-center gap-2 bg-gray-900 dark:bg-white text-white dark:text-black px-4 py-2 rounded-lg text-sm font-medium hover:bg-gray-800 dark:hover:bg-gray-100 transition-colors">
  <span>Add New...</span>
  <ChevronDown className="w-3.5 h-3.5" />
</button>
```

#### Compound Button with Icon and Text
```tsx
<button className="flex items-center gap-1.5 text-xs font-medium px-3 py-1.5 rounded-md border border-gray-200 dark:border-white/20 text-gray-600 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors">
  <Eye className="w-3 h-3" />
  Open in Observability
</button>
```

### Button Size Reference

| Size | Padding | Text | Radius | Usage |
|------|---------|------|--------|-------|
| Small | `px-3 py-1` | `text-xs` | `rounded-md` | Inline in cards |
| Default | `px-4 py-2` | `text-sm` | `rounded-lg` | Standard actions |
| Compact | `px-3 py-1.5` | `text-xs` | `rounded-md` | Filters, secondary |
| Icon | `p-2` | — | `rounded-lg` | Square icon button |
| Tiny icon | `p-1` | — | `rounded-md` | Hover-reveal actions |

---

### Inputs & Form Elements

#### Standard Text Input
```tsx
<input
  type="text"
  placeholder="Enter project name..."
  className="w-full px-3 py-2 text-sm rounded-lg border border-gray-200 dark:border-white/10 bg-white dark:bg-[#0a0a0a] text-gray-900 dark:text-white placeholder-gray-400 dark:placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-gray-300 dark:focus:ring-gray-700 focus:border-transparent transition-all"
/>
```

#### Error Input
```tsx
<div className="relative">
  <input
    type="text"
    defaultValue="invalid-domain"
    className="w-full px-3 py-2 text-sm rounded-lg border border-red-300 dark:border-red-800 bg-white dark:bg-[#0a0a0a] text-gray-900 dark:text-white focus:outline-none focus:ring-2 focus:ring-red-200 dark:focus:ring-red-900 transition-all"
  />
  <AlertCircle className="absolute right-3 top-1/2 -translate-y-1/2 w-4 h-4 text-red-500" />
</div>
<p className="text-xs text-red-500 mt-1">Error message here.</p>
```

#### Search Input with Icon + Shortcut
```tsx
<div className="relative">
  <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
  <input
    type="text"
    placeholder="Search Projects..."
    className="w-full pl-10 pr-12 py-2 text-sm rounded-lg border border-gray-200 dark:border-white/10 bg-white dark:bg-[#0a0a0a] text-gray-900 dark:text-white placeholder-gray-400 dark:placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-gray-300 dark:focus:ring-gray-700 focus:border-transparent transition-all"
  />
</div>
```

#### Sidebar Compact Search
```tsx
<div className="flex items-center gap-2 px-2.5 py-1.5 rounded-md border border-gray-200 dark:border-white/10 bg-white dark:bg-black hover:border-gray-300 dark:hover:border-white/30 transition-colors cursor-text">
  <Search className="w-3.5 h-3.5 text-gray-400" />
  <span className="text-xs text-gray-400 flex-1">Find...</span>
  <kbd className="text-[10px] text-gray-400 bg-gray-100 dark:bg-gray-800 px-1.5 py-0.5 rounded font-mono">F</kbd>
</div>
```

#### Dropdown/Select Trigger
```tsx
<button className="flex items-center gap-2 px-3 py-1.5 rounded-md border border-gray-200 dark:border-white/20 text-sm text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-800 transition-colors">
  <span>Last 30 Days</span>
  <ChevronDown className="w-3.5 h-3.5 text-gray-400" />
</button>
```

#### Date Range Picker Trigger
```tsx
<button className="flex items-center gap-2 px-3 py-1.5 rounded-md border border-gray-200 dark:border-white/20 text-sm text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-800 transition-colors">
  <CalendarDays className="w-3.5 h-3.5 text-gray-400" />
  <span>Jun 1, 13:00 – Jul 1</span>
</button>
```

#### Keyboard Shortcut Pill
```tsx
<kbd className="text-[10px] text-gray-400 bg-gray-100 dark:bg-gray-800 px-1.5 py-0.5 rounded font-mono">
  ⌘K
</kbd>
```

#### Form Label (Uppercase)
```tsx
<label className="block text-xs font-medium text-gray-600 dark:text-gray-400 uppercase tracking-wider">
  Field Label
</label>
```

---

### Badges & Pills

#### Default Badge
```tsx
<span className="inline-flex items-center px-2 py-0.5 text-xs font-medium rounded-full bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-400">
  Hobby
</span>
```

#### Blue Badge
```tsx
<span className="inline-flex items-center px-2 py-0.5 text-xs font-medium rounded-full bg-blue-50 text-blue-600 dark:bg-blue-950 dark:text-blue-400">
  Beta
</span>
```

#### Outline Badge
```tsx
<span className="inline-flex items-center px-2 py-0.5 text-xs font-medium rounded-full border border-gray-200 text-gray-500 dark:border-white/20 dark:text-gray-500">
  v14.2.1
</span>
```

#### Success Badge
```tsx
<span className="inline-flex items-center px-2 py-0.5 text-xs font-medium rounded-full bg-green-50 text-green-700 dark:bg-green-950 dark:text-green-400">
  Ready
</span>
```

#### Count Badge (for nav items)
```tsx
<span className="text-xs bg-blue-100 text-blue-700 dark:bg-blue-950 dark:text-blue-400 px-1.5 py-0.5 rounded-full font-medium">
  6
</span>
```

#### GitHub Repo Badge
```tsx
<span className="inline-flex items-center gap-1.5 text-xs bg-gray-100 dark:bg-gray-800 text-gray-700 dark:text-gray-300 px-2 py-1 rounded-md font-mono">
  <GitHubIcon className="w-3 h-3" />
  username/repo-name
</span>
```

---

### Status Indicators

#### Status Dot
```tsx
// Green = Online/Ready
<span className="w-2 h-2 rounded-full bg-green-500" />

// Yellow = Building/Warning
<span className="w-2 h-2 rounded-full bg-yellow-500" />

// Red = Error
<span className="w-2 h-2 rounded-full bg-red-500" />

// Gray = Queued/Disabled
<span className="w-2 h-2 rounded-full bg-gray-400 dark:bg-gray-600" />
```

#### Deployment Success Ring (with pulse glow)
```tsx
<div className="w-7 h-7 rounded-full border-2 border-blue-500 flex items-center justify-center pulse-glow">
  <Check className="w-3.5 h-3.5 text-blue-500" />
</div>
```

#### Progress Ring (SVG)
```tsx
function ProgressRing({ progress, size = 20, strokeWidth = 2 }: {
  progress: number; size?: number; strokeWidth?: number;
}) {
  const radius = (size - strokeWidth) / 2;
  const circumference = radius * 2 * Math.PI;
  const offset = circumference - (progress / 100) * circumference;

  return (
    <svg width={size} height={size} className="shrink-0" viewBox={`0 0 ${size} ${size}`}>
      <circle cx={size/2} cy={size/2} r={radius} fill="none"
        className="stroke-gray-200 dark:stroke-gray-700" strokeWidth={strokeWidth} />
      <circle cx={size/2} cy={size/2} r={radius} fill="none"
        className="stroke-blue-500 progress-ring-circle"
        strokeWidth={strokeWidth} strokeDasharray={circumference}
        strokeDashoffset={offset} strokeLinecap="round"
        style={{ "--progress-offset": offset } as React.CSSProperties} />
    </svg>
  );
}
```

#### Default Spinner (Loading)
```tsx
<div className="w-8 h-8 rounded-full border-2 border-gray-300 dark:border-white/30 flex items-center justify-center animate-spin">
  <div className="w-3 h-0.5 bg-gray-400 rounded-full" />
</div>
```

#### Dot Loading Animation
This animation relies on the `.loader` CSS class defined in `globals.css`.
```tsx
<div className="loader h-8 transform scale-50 origin-top"></div>
```

#### Progress Line Animation
This animated progress bar relies on the `.progress-line` CSS class. It automatically adapts to light/dark mode by referencing `var(--foreground)`.
```tsx
<div className="w-full flex items-center justify-start">
  <div className="w-full relative">
    <div className="progress-line"></div>
  </div>
</div>
```

---

### Cards

#### Standard Project Card Structure
```tsx
<div className="group rounded-lg border border-gray-200 dark:border-white/10 bg-white dark:bg-[#0a0a0a] hover:border-gray-300 dark:hover:border-white/30 transition-all duration-200">
  {/* Header: Logo + Title + Status */}
  <div className="p-4 flex items-start justify-between">
    <div className="flex items-center gap-3 min-w-0">
      <div className="w-9 h-9 rounded-full bg-gradient-to-br from-X to-Y flex items-center justify-center text-white text-sm font-bold shrink-0">
        A
      </div>
      <div className="min-w-0">
        <p className="text-sm font-semibold text-gray-900 dark:text-white truncate">Title</p>
        <p className="text-xs text-gray-500 truncate">subtitle</p>
      </div>
    </div>
    <div className="flex items-center gap-1.5 shrink-0">
      {/* Status ring */}
      {/* Hover-reveal menu button */}
      <button className="p-1 rounded-md hover:bg-gray-100 dark:hover:bg-gray-800 text-gray-400 transition-colors opacity-0 group-hover:opacity-100">
        <MoreHorizontal className="w-4 h-4" />
      </button>
    </div>
  </div>

  {/* Badge row */}
  <div className="px-4 pb-3">{/* repo badge */}</div>

  {/* Footer: description + metadata */}
  <div className="px-4 pb-4">
    <p className="text-sm text-gray-700 dark:text-gray-300 truncate">Description text</p>
    <div className="flex items-center gap-1.5 mt-1.5 text-xs text-gray-500">
      <span>Date</span>
      <span>on</span>
      <GitBranch className="w-3 h-3" />
      <span className="font-mono">main</span>
    </div>
  </div>
</div>
```

#### Card Anatomy Rules

1. **Padding**: `p-4` for card sections.
2. **Hover**: Change border color `hover:border-gray-300 dark:hover:border-white/30`.
3. **Group hover**: Use `group` on card root and `group-hover:opacity-100` to reveal action buttons.
4. **Avatar/Logo**: `w-9 h-9 rounded-full` with gradient backgrounds.
5. **Always `min-w-0`** on flex children that contain truncated text.

---

### Navigation

#### Sidebar Nav Item (Active)
```tsx
<button className="w-full flex items-center gap-2.5 px-2.5 py-1.5 rounded-md text-sm bg-gray-100 dark:bg-gray-900 text-gray-900 dark:text-white font-medium transition-all duration-150">
  <Icon className="w-4 h-4 shrink-0" />
  <span className="truncate">Item Name</span>
</button>
```

#### Sidebar Nav Item (Inactive)
```tsx
<button className="w-full flex items-center gap-2.5 px-2.5 py-1.5 rounded-md text-sm text-gray-600 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-gray-900 hover:text-gray-900 dark:hover:text-gray-200 transition-all duration-150">
  <Icon className="w-4 h-4 shrink-0" />
  <span className="truncate">Item Name</span>
</button>
```

#### Tab Navigation (Underline Style)
```tsx
<button className={`relative px-4 py-2.5 text-sm font-medium transition-colors ${
  isActive
    ? "text-gray-900 dark:text-white"
    : "text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-300"
}`}>
  Tab Label
  {isActive && (
    <span className="absolute bottom-0 left-0 right-0 h-0.5 bg-gray-900 dark:bg-white rounded-full" />
  )}
</button>
```

#### Sidebar Divider
```tsx
<div className="h-px bg-gray-200 dark:bg-gray-800 my-2 mx-1" />
```

---

### Tables & Data Display

#### Table with Header
```tsx
<div className="rounded-lg border border-gray-200 dark:border-white/10 bg-white dark:bg-[#0a0a0a] overflow-hidden">
  {/* Header Row */}
  <div className="flex items-center justify-between px-4 py-3 border-b border-gray-200 dark:border-white/10 text-xs font-medium text-gray-500 dark:text-gray-400">
    <span>Column 1</span>
    <span>Column 2</span>
  </div>

  {/* Data Rows */}
  <div className="flex items-center justify-between px-4 py-3 border-b border-gray-100 dark:border-white/5 hover:bg-gray-50 dark:hover:bg-gray-900/50 transition-colors last:border-b-0">
    <div className="flex items-center gap-3">
      <ProgressRing progress={15} size={18} strokeWidth={2} />
      <span className="text-sm text-gray-700 dark:text-gray-300">Row Label</span>
    </div>
    <span className="text-sm text-gray-500 dark:text-gray-400 font-mono">Value</span>
  </div>
</div>
```

---

### Accordion / Expandable Sections

```tsx
<div className="rounded-lg border border-gray-200 dark:border-white/10 bg-white dark:bg-[#0a0a0a] overflow-hidden transition-all duration-200">
  {/* Trigger */}
  <button
    onClick={() => setExpanded(!expanded)}
    className="w-full flex items-center justify-between p-4 text-left hover:bg-gray-50 dark:hover:bg-gray-900/50 transition-colors"
  >
    <h3 className="text-sm font-semibold text-gray-900 dark:text-white">Section Title</h3>
    <div className="flex items-center gap-3 shrink-0">
      {/* Action buttons */}
    </div>
  </button>

  {/* Content (shown when expanded) */}
  {expanded && (
    <div className="px-4 pb-4 accordion-content-open">
      <p className="text-sm text-gray-500 dark:text-gray-400 mb-3">
        Description with <a href="#" className="text-blue-500 hover:underline">Learn More</a>
      </p>
    </div>
  )}
</div>
```

---

### Alert / Info Banners

```tsx
<div className="flex items-center gap-2 px-4 py-3 rounded-lg border border-gray-200 dark:border-white/10 bg-gray-50 dark:bg-gray-900/50">
  <Info className="w-4 h-4 text-gray-400 shrink-0" />
  <p className="text-sm text-gray-600 dark:text-gray-400">
    Info message with <span className="font-semibold text-gray-900 dark:text-white">emphasized text</span>.
  </p>
</div>
```

---

### Empty States

```tsx
<div className="flex flex-col items-center justify-center py-8 text-center">
  <div className="w-10 h-10 rounded-full border border-gray-200 dark:border-white/20 flex items-center justify-center mb-4">
    <Globe className="w-5 h-5 text-gray-400" />
  </div>
  <p className="text-xs text-gray-500 dark:text-gray-400 leading-relaxed">
    Empty state description text<br />
    spanning multiple lines.
  </p>
</div>
```

---

### Promo / CTA Cards

#### Dismissible Promo Card
```tsx
<div className="p-3 rounded-lg border border-gray-200 dark:border-white/10 bg-white dark:bg-black relative">
  <button className="absolute top-2 right-2 text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 transition-colors">
    <X className="w-3.5 h-3.5" />
  </button>
  <p className="text-sm font-medium text-gray-900 dark:text-white pr-4">Card Title</p>
  <p className="text-xs text-gray-500 dark:text-gray-400 mt-1 leading-relaxed">Description text.</p>
  <button className="mt-2.5 w-full text-xs font-medium py-1.5 px-3 rounded-md bg-gray-900 dark:bg-white text-white dark:text-black hover:bg-gray-800 dark:hover:bg-gray-100 transition-colors">
    CTA Button
  </button>
</div>
```

#### Centered CTA Card
```tsx
<div className="rounded-lg border border-gray-200 dark:border-white/10 bg-white dark:bg-[#0a0a0a] p-6 text-center">
  <h3 className="text-sm font-semibold text-gray-900 dark:text-white mb-1">Heading</h3>
  <p className="text-xs text-gray-500 dark:text-gray-400 leading-relaxed mb-4">
    Description spanning<br />two lines.
  </p>
  <button className="text-sm font-medium px-4 py-1.5 rounded-md border border-gray-200 dark:border-white/20 text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-800 transition-colors">
    Action
  </button>
</div>
```

---

### Toggle Groups

```tsx
<div className="flex items-center border border-gray-200 dark:border-white/10 rounded-lg overflow-hidden">
  <button className={`p-2 transition-colors ${
    isActive ? "bg-gray-100 dark:bg-gray-800 text-gray-900 dark:text-white"
             : "text-gray-400 hover:bg-gray-50 dark:hover:bg-gray-900"
  }`}>
    <LayoutGrid className="w-4 h-4" />
  </button>
  <button className={`p-2 transition-colors border-l border-gray-200 dark:border-white/10 ${
    !isActive ? "bg-gray-100 dark:bg-gray-800 text-gray-900 dark:text-white"
              : "text-gray-400 hover:bg-gray-50 dark:hover:bg-gray-900"
  }`}>
    <List className="w-4 h-4" />
  </button>
</div>
```

---

### Search Components

#### Full-Width Search Bar with Controls
```tsx
<div className="flex items-center gap-3">
  <div className="flex-1 relative">
    <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
    <input type="text" placeholder="Search..." className="w-full pl-10 pr-4 py-2 text-sm rounded-lg border border-gray-200 dark:border-white/10 bg-white dark:bg-[#0a0a0a] ..." />
  </div>
  <button className="p-2 rounded-lg border ...">{/* Filter icon */}</button>
  {/* View Toggle */}
  {/* Add New Button */}
</div>
```

---

## 12. Responsive Rules

### Breakpoint Strategy

| Breakpoint | Behavior |
|-----------|----------|
| `< 768px` | Sidebar collapses to hamburger. Cards stack single-column. |
| `768px – 1024px` | Sidebar visible, content area single-column. |
| `> 1024px` | Full layout: sidebar + 2-column content. |

### Responsive Classes Pattern

```tsx
// Card grid
<div className="grid grid-cols-1 md:grid-cols-2 gap-4">

// Two-column layout
<div className="flex flex-col lg:flex-row gap-6">
  <div className="w-full lg:w-[320px] shrink-0">{/* Left */}</div>
  <div className="flex-1 min-w-0">{/* Right */}</div>
</div>

// Sidebar
<aside className="hidden md:flex w-[220px] shrink-0">{/* Sidebar */}</aside>
```

---

## 13. Interaction States Reference Table

| Element | Default | Hover | Active/Pressed | Focus | Disabled |
|---------|---------|-------|----------------|-------|----------|
| **Primary Button** | `bg-gray-900 text-white` | `bg-gray-800` | `bg-gray-700` | `ring-2 ring-gray-400` | `opacity-50 cursor-not-allowed` |
| **Secondary Button** | `border-gray-200 text-gray-700` | `bg-gray-50` | `bg-gray-100` | `ring-2 ring-gray-300` | `opacity-50` |
| **Input** | `border-gray-200` | `border-gray-300` | — | `ring-2 ring-gray-300 border-transparent` | `bg-gray-50 text-gray-400` |
| **Nav Item** | `text-gray-600` | `bg-gray-50 text-gray-900` | `bg-gray-100` | — | — |
| **Card** | `border-gray-200` | `border-gray-300` | — | — | `opacity-60` |
| **Table Row** | transparent | `bg-gray-50` | — | — | — |
| **Link** | `text-blue-500` | `underline` | — | `ring-2 ring-blue-300` | `text-gray-400` |

*(All values have `dark:` equivalents following the mapping in Section 8)*

---

## 14. Do's and Don'ts

### ✅ DO

- Use `text-sm` as default text size everywhere.
- Pair every light class with its `dark:` equivalent.
- Use `transition-colors` on all interactive elements.
- Use `shrink-0` on icons inside flex containers.
- Use `truncate` on text that might overflow.
- Use `min-w-0` on flex children containing truncated text.
- Use `font-mono` for data values, file sizes, dates.
- Use 1px borders (`border`) for structure, never shadows.
- Keep buttons small (`text-sm`, compact padding).
- Use `rounded-lg` on all containers and buttons.
- Show secondary actions on hover with `opacity-0 group-hover:opacity-100`.

### ❌ DON'T

- Use `text-base` (16px) — always use `text-sm` (14px).
- Use `shadow-md`, `shadow-lg`, or any box-shadow for layout.
- Use vivid colors on backgrounds — only grays, black, white.
- Use `rounded-none` on interactive elements.
- Use `font-bold` on body text — reserve for headings only.
- Use inline styles for colors — always use Tailwind classes.
- Animate layout properties (`width`, `height`) — use `opacity` + `transform`.
- Create thick borders — always `border` (1px), never `border-2` except status rings.
- Use generic sans-serif — always use Geist/Inter.
- Forget hover states — every clickable element needs visual feedback.

---

## 15. Full CSS Foundation

Copy this into your `globals.css` for any new project:

```css
@import "tailwindcss";

@custom-variant dark (&:where(.dark, .dark *));

@theme inline {
  --color-background: var(--background);
  --color-foreground: var(--foreground);
  --font-sans: var(--font-geist-sans);
  --font-mono: var(--font-geist-mono);
}

:root {
  --background: #ffffff;
  --foreground: #0a0a0a;
}

@media (prefers-color-scheme: dark) {
  :root {
    --background: #000000;
    --foreground: #ededed;
  }
}

* { box-sizing: border-box; }

body {
  background: var(--background);
  color: var(--foreground);
  font-family: var(--font-geist-sans), -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
}

/* Scrollbar */
::-webkit-scrollbar { width: 6px; height: 6px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: #d4d4d4; border-radius: 3px; }
.dark ::-webkit-scrollbar-thumb { background: #333333; }

/* Animations */
@keyframes fadeIn {
  from { opacity: 0; transform: translateY(4px); }
  to   { opacity: 1; transform: translateY(0); }
}
.animate-fade-in { animation: fadeIn 0.3s ease-out forwards; }

.stagger-1 { animation-delay: 0.05s; }
.stagger-2 { animation-delay: 0.1s; }
.stagger-3 { animation-delay: 0.15s; }
.stagger-4 { animation-delay: 0.2s; }
.stagger-5 { animation-delay: 0.25s; }
.stagger-6 { animation-delay: 0.3s; }

@keyframes accordion-open {
  from { max-height: 0; opacity: 0; }
  to   { max-height: 500px; opacity: 1; }
}
.accordion-content-open { animation: accordion-open 0.3s ease-out forwards; overflow: hidden; }

@keyframes pulse-glow {
  0%, 100% { box-shadow: 0 0 0 0 rgba(0, 112, 243, 0.4); }
  50%      { box-shadow: 0 0 0 4px rgba(0, 112, 243, 0); }
}
.pulse-glow { animation: pulse-glow 2s infinite; }

@keyframes progress-ring {
  0%   { stroke-dashoffset: 100; }
  100% { stroke-dashoffset: var(--progress-offset, 0); }
}
.progress-ring-circle {
  animation: progress-ring 1s ease-out forwards;
  transform: rotate(-90deg);
  transform-origin: 50% 50%;
}
```

---

## 16. Quick-Start Prompt Template

Use this prompt when starting a new project with any AI agent:

```
You are building a web page using the Geist Design System (Vercel-style).

RULES:
- Tech: Next.js + Tailwind CSS v4 + lucide-react icons + TypeScript
- Font: Geist Sans via next/font/google. Default text is text-sm (14px).
- Colors: Monochromatic only. Light = white bg, gray-200 borders, gray-900 text.
  Dark = black bg, gray-800 borders, gray-100 text. Accent = blue-500 for links only.
- Borders: 1px borders define all structure. No box-shadows. Cards/buttons = rounded-lg.
- Dark mode: Class-based via `dark:` prefix. Every visual class must have a dark: pair.
  Primary buttons invert: bg-gray-900/text-white → dark:bg-white/dark:text-black.
- Animations: transition-colors on all interactives. fadeIn for card entrances.
  Stagger with animation-delay. Accordion open/close with max-height animation.
- Typography: text-sm body, text-xs metadata, text-lg headings. font-mono for data.
- Icons: lucide-react, w-4 h-4 default, always shrink-0. text-gray-400 default color.
- Interaction: hover:bg-gray-50/dark:hover:bg-gray-900. Group-hover reveal for actions.
- Layout: Sidebar w-[220px] + flex-1 content. Content max-w-[1200px] mx-auto.
- NEVER use: shadows, vibrant colors on containers, text-base as default,
  rounded-none on UI elements, font-bold on body text.

Refer to the DESIGN_SYSTEM.md file for detailed component code, token tables,
and the full CSS foundation.
```

---

*Generated from the Geist UI demo at `/Users/shajidshahriar/Desktop/template`. Last updated: July 2026.*
