"""普通主线与活动出击前的智能换船；一轮只使用一个候补舰队。"""

from copy import deepcopy

from module.combat.emotion import Emotion
from module.config.time_source import now
from module.exception import FleetManagementError, GameStuckError, GameTooManyClickError, RequestHumanTakeover
from module.logger import logger
from module.retire.fleet_management import FleetManagement
from module.runtime.fleet_rotation import (ROTATION_PATH, active_fleets, begin_round, calibration_changes,
                                         minimum_morale, parse_rules, rank_sources, recorded_morale, supported)


def _required(emotion, role, battle):
    order = emotion.config.Fleet_FleetOrder
    counts = {'fleet1_mob_fleet2_boss': (max(0, battle - 1), 1),
              'fleet1_boss_fleet2_mob': (1, max(0, battle - 1)),
              'fleet1_all_fleet2_standby': (battle, 0), 'fleet1_standby_fleet2_all': (0, battle)}[order]
    tracker = emotion.public_fleet if emotion.using_public else emotion.fleets[role - 1]
    if counts[role - 1] == 0:
        return tracker, None
    reduce = counts[role - 1] * emotion.reduce_per_battle_before_entering
    if tracker.control == 'keep_exp_bonus':
        reduce = min(29, reduce)
    return tracker, tracker.limit + reduce


def preflight_rotation(runner, battle, mode):
    """在原有心情检查之前轮换，恢复选关页后仍走正常心情及入场流程。

    Pages:
        in: 普通主线或活动选关页、自动搜索续战菜单
        out: 已操作时返回当前任务选关页；未操作时保持原页面
    """
    campaign = runner.campaign
    config = campaign.config
    if (config.cross_get(ROTATION_PATH + '.Enabled', False) is not True
            or not supported(config.task.command, mode, runner.folder, runner.stage)
            or campaign.config.Campaign_Mode != 'normal'):
        return False
    try:
        rules = parse_rules(config.cross_get(ROTATION_PATH + '.Rules', '{}'))
    except ValueError as exc:
        raise FleetManagementError(str(exc)) from exc
    active = active_fleets(config)
    states = deepcopy(config.cross_get(ROTATION_PATH + '.State', {}))
    if not isinstance(states, dict):
        states = {}
    # 其他任务也可能使用本轮的来源队；它的舰船已改变，不能拿旧心情继续出击。
    for target, progress in states.items():
        if (isinstance(progress, dict) and progress.get('source') in active.values()
                and int(target) not in active.values()):
            raise FleetManagementError(f'第{target}舰队的轮换尚未完成，来源队不能使用旧心情出击')
    selected = {role: actual for role, actual in active.items()
                if rules.get(str(actual), {}).get('slots') and rules[str(actual)].get('sources')}
    manager = None
    emotion = campaign.emotion
    emotion.update()
    visited = False
    context = runner.__dict__.setdefault('_fleet_rotation_context', {})
    task = config.task.command
    if context.get('task') != task:
        context.clear()
        context['task'] = task
    fresh = not context.get('scanned', False)
    try:
        fresh_readings = {}
        if fresh:
            manager = FleetManagement(config=config, device=runner.device)
            visited = True
            manager.run(update_emotion=True)
            result = config.cross_get('FleetInfo.FleetInfo.Result', {})
            fresh_readings = {fleet: recorded_morale(result, fleet) for fleet in range(1, 7)}
            config.cross_set_many(calibration_changes(config.data,
                {fleet: values for fleet, values in fresh_readings.items() if values}, now()))
            context['scanned'] = True
        for role, fleet in selected.items():
            # 前一个角色可能刚从本角色的实际舰队换走舰船，不能继续使用旧快照。
            emotion.update()
            rule = rules[str(fleet)]
            previous = states.get(str(fleet), {})
            if not isinstance(previous, dict):
                previous = {}
            tracker, needed = _required(emotion, role, battle)
            if needed is None:
                continue
            continuing = bool(previous.get('source') or previous.get('pending'))
            if not fresh and not continuing and emotion.is_calculate and tracker.state is not None and tracker.lower >= needed:
                continue
            visited = True
            if manager is None:
                manager = FleetManagement(config=config, device=runner.device)
            if continuing:
                try:
                    progress = begin_round(rule, previous)
                except ValueError as exc:
                    raise FleetManagementError(str(exc)) from exc
            else:
                readings = fresh_readings[fleet] if fresh else manager.read_fleet_morale(fleet)
                minimum = minimum_morale(readings)
                if minimum is None:
                    logger.warning('目标队实际心情未读全，不执行换船，沿用原出击检查')
                    continue
                config.cross_set_many(calibration_changes(config.data, {fleet: readings}, now()))
                if minimum >= needed:
                    continue
                candidates = {source: fresh_readings[source] if fresh else manager.read_fleet_morale(source)
                              for source in rule['sources']}
                config.cross_set_many(calibration_changes(config.data, candidates, now()))
                order = rank_sources(rule, candidates, needed)
                progress = begin_round(rule, previous, order)
                if progress is None:
                    logger.warning('候补队没有满足心情及对应舰位的舰船，保留当前编队并沿用原心情检查')
                    continue
                logger.attr('候补队心情优先级', order)
            source = progress['source']
            states[str(fleet)] = progress
            config.cross_set(ROTATION_PATH + '.State', states)
            logger.hr(f'智能换船：第{fleet}舰队使用候补第{source}舰队', level=1)
            paused = False
            for slot in rule['slots']:
                if slot in progress['done']:
                    continue
                if config.cross_get(ROTATION_PATH + '.Enabled', False) is not True:
                    paused = True
                    break
                source_slot = slot
                # 先记下即将执行的互换。进程中断后不能猜测已交换还是尚未交换。
                progress['pending'] = dict(slot=slot, source_slot=source_slot)
                config.cross_set(ROTATION_PATH + '.State', states)
                manager.swap_slots(source, source_slot, fleet, slot)
                progress['done'].append(slot)
                progress['pending'] = None
                config.cross_set(ROTATION_PATH + '.State', states)
            # 如果最后读取失败，已完成舰位仍保留；重试仅补核对，不再换一遍。
            readings = {fleet: manager.read_fleet_morale(fleet, first_slot=1),
                        source: manager.read_fleet_morale(source)}
            changes = calibration_changes(config.data, readings, now())
            config.cross_set_many(changes)
            if any(minimum_morale(values) is None for values in readings.values()):
                raise FleetManagementError('互换已经完成，最低心情尚未读全；下次仅重试核对')
            if not paused:
                states[str(fleet)] = dict(last_source=source, completed_at=now().isoformat(timespec='seconds'))
            config.cross_set(ROTATION_PATH + '.State', states)
            fresh = False
            book = emotion.map_is_2x_book
            emotion = Emotion(config)
            emotion.map_is_2x_book = book
            # 本轮到此结束，心情仍低则交给原有延迟逻辑，不连续轮换下一来源。
        if visited:
            config.update()
            runner.config.update()
            campaign.emotion = Emotion(config)
            campaign.emotion.map_is_2x_book = emotion.map_is_2x_book
            campaign.ensure_campaign_ui(name=runner.stage, mode=mode, skip_first_screenshot=False)
            campaign.map_get_info()
        return visited
    except (GameStuckError, GameTooManyClickError, RequestHumanTakeover) as exc:
        changed = {number: {1: None} for target, progress in states.items() if isinstance(progress, dict)
                   and progress.get('source') and (progress.get('done') or progress.get('pending'))
                   for number in (int(target), progress['source'])}
        if changed:
            config.cross_set_many(calibration_changes(config.data, changed, now()))
        if isinstance(exc, FleetManagementError):
            raise
        raise FleetManagementError(f'智能换船未确认完成，保留轮换记录以避免重复交换：{exc}') from exc
