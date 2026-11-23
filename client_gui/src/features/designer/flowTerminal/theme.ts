// The terminal's font and colors follow the FlowGate dark navy windows (0001-R §3.4-3: no theme choice in this stage).
import type { ITheme } from '@xterm/xterm'

// D2Coding / Nanum Gothic Coding keep Hangul at two columns on screens that have them.
export const TERMINAL_FONT = "'Cascadia Mono', 'D2Coding', 'Nanum Gothic Coding', Consolas, 'SF Mono', Menlo, 'DejaVu Sans Mono', monospace"

export const TERMINAL_THEME: ITheme = {
  background: '#0a1626', foreground: '#d8e8f5', cursor: '#58bcff', cursorAccent: '#0a1626',
  selectionBackground: '#2a5b88', selectionForeground: '#ffffff',
  black: '#14243a', red: '#ff7b86', green: '#5fdcaa', yellow: '#f2cf6b', blue: '#58bcff', magenta: '#c49bff', cyan: '#4fd6d0', white: '#d8e8f5',
  brightBlack: '#5c7690', brightRed: '#ffa0a8', brightGreen: '#8ff0c8', brightYellow: '#ffe39a', brightBlue: '#8ad2ff',
  brightMagenta: '#dbbfff', brightCyan: '#8cf0ea', brightWhite: '#ffffff',
}
