/** 图形调度是系统编辑页，不伪装成可执行的游戏任务。 */
export const SCHEDULER_EDITOR = 'SchedulerProgram'
export function taskNavItems(key: string | null, tasks: string[]) {
  return key === 'Alas' ? [tasks[0]!, SCHEDULER_EDITOR, ...tasks.slice(1)] : tasks
}

/** 任务名：调度器编辑页用系统名，其余取任务表里的显示名。 */
export function taskLabel(task: string, ui: (key: 'nav.schedulerProgram') => string, t: (key: string) => string) {
  return task === SCHEDULER_EDITOR ? ui('nav.schedulerProgram') : t(`Task.${task}.name`)
}
