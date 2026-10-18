/** 优先级沿用英文任务标识；翻译只作为可读注释显示，不参与调度。 */
export function taskPriorityText(value: string): string {
  return value.replace(/[＞﹥›˃ᐳ❯]/g, '>').split(/\r?\n/)
    .flatMap(line => line.split('#', 1)[0]!.split('>'))
    .map(task => task.trim()).filter(Boolean).join('\n> ')
}

export function translatedTaskPriority(value: string, t: (key: string) => string): string {
  return taskPriorityText(value).split('\n').map(line => {
    const task = line.replace(/^>\s*/, '')
    if (!task) return line
    const name = t(`Task.${task}.name`)
    return name && name !== task ? `${line} # ${name.replace(/[\r\n]+/g, ' ')}` : line
  }).join('\n')
}
