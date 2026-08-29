# MASSHINE visual system

The interface uses the repository's approved archival paper register: warm near-white paper, graphite ink, fine rules, and one restrained rust accent reserved for the researcher's direction.

```css
:root {
  --bg: oklch(98.6% 0.005 85);
  --surface: oklch(100% 0 0);
  --fg: oklch(21% 0.012 60);
  --muted: oklch(46% 0.012 60);
  --border: oklch(91.5% 0.006 75);
  --accent: oklch(53% 0.13 30);
}
```

- Display: `"Iowan Old Style", "Palatino Linotype", Palatino, Georgia, serif`
- Body: `-apple-system, BlinkMacSystemFont, "SF Pro Text", system-ui, sans-serif`
- Mono: `ui-monospace, "SF Mono", Menlo, monospace`

Observed rules:

1. Material, claims, and the evolving account use serif type; controls and provenance use quiet sans.
2. Whitespace and hairlines establish hierarchy; containers are not nested into dashboard cards.
3. The accent marks researcher-authored direction only, never machine output or generic status.
4. Evidence references are text doors into verbatim passages, not decorative chips.
5. The paper surface is the destination for scrutiny; analytical views stay lighter and more operational.
