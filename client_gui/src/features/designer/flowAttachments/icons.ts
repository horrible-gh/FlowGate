// The line icons of the final mockup (24x24, stroke 1.8, round joins).
import { defineComponent, h, type PropType } from 'vue'

const PATHS = {
  clip: ['M21 11.4 12.4 20a5.2 5.2 0 0 1-7.3-7.3l9-9A3.5 3.5 0 1 1 19 8.6l-9.2 9.2a1.8 1.8 0 0 1-2.5-2.5l8.3-8.3'],
  folder: ['M3 6h6l2 2h10v11H3z'],
  flow: ['M7 12h4a4 4 0 0 0 4-4V7M11 12a4 4 0 0 1 4 4v1', 'M3 12a2 2 0 1 0 4 0a2 2 0 1 0-4 0', 'M16 5a2 2 0 1 0 4 0a2 2 0 1 0-4 0', 'M16 19a2 2 0 1 0 4 0a2 2 0 1 0-4 0'],
  file: ['M13 3H5v18h14V9zM13 3v6h6M8 13h8M8 17h8'],
  image: ['M5 3h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2z', 'M7 8.5a1.5 1.5 0 1 0 3 0a1.5 1.5 0 1 0-3 0', 'm4 18 5-5 3 3 4-5 4 5'],
  plus: ['M12 4v16M4 12h16'],
  close: ['M5 5 19 19M19 5 5 19'],
  search: ['M4 10.8a6.8 6.8 0 1 0 13.6 0a6.8 6.8 0 1 0-13.6 0', 'm16 16 5 5'],
  upload: ['M12 16V3m-5 5 5-5 5 5M4 16v4h16v-4'],
  download: ['M12 4v13m-5-5 5 5 5-5M4 18v3h16v-3'],
  undo: ['M8.5 5 4 9.5 8.5 14', 'M4.5 9.5h10a5 5 0 0 1 0 10H11'],
  trash: ['M4 7h16M9 7V4h6v3m-9 0 1 14h10l1-14M10 11v6M14 11v6'],
} as const
export type IconName = keyof typeof PATHS

export const FaIcon = defineComponent({
  name: 'FaIcon',
  props: { name: { type: String as PropType<IconName>, required: true }, size: { type: Number, default: 16 } },
  setup(props) {
    return () => h('svg', {
      class: 'fa-icon', viewBox: '0 0 24 24', width: props.size, height: props.size, 'aria-hidden': 'true',
      fill: 'none', stroke: 'currentColor', 'stroke-width': 1.8, 'stroke-linecap': 'round', 'stroke-linejoin': 'round',
    }, PATHS[props.name].map(d => h('path', { d })))
  },
})
