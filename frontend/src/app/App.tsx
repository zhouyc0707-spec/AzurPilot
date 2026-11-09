/** 两族外壳独立，旧界面定制不被上游现代布局覆盖。 */
import {useApp} from './context'
import {usesLegacyLayout} from './theme'
import {LegacyApp} from './LegacyApp'
import {ModernApp} from './ModernApp'
export {CreateInstance} from './ModernApp'
export function App() {return usesLegacyLayout(useApp().theme) ? <LegacyApp/> : <ModernApp/>}
