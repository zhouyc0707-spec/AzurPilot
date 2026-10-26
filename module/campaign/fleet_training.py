"""等级/好感培养联动既有同号舰位换船，不重新实现心情调度。"""

from copy import deepcopy

from module.config.time_source import now
from module.equipment.fleet_rotation import FleetRotationEquipment
from module.exception import FleetManagementError, ScriptEnd
from module.logger import logger
from module.retire.fleet_management import FleetManagement
from module.retire.fleet_training import FleetTrainingReader
from module.runtime.fleet_equipment import EQUIPMENT_PATH
from module.runtime.fleet_rotation import ROTATION_PATH, active_fleets, calibration_changes, parse_rules, supported
from module.runtime.fleet_training import TRAINING_PATH, due, excluded, goals, identity, interval, observe, ships, state


class TrainingSession:
    def __init__(self, runner, manager, context, rules, selected):
        self.runner, self.manager, self.context = runner, manager, context
        self.config = runner.campaign.config
        self.rules, self.selected = rules, selected
        self.targets = goals(self.config)
        self.saved = state(self.config)
        self.reader = FleetTrainingReader(manager)
        self.inventory_fleets = set(selected.values())
        self.equipment_used = False

    @property
    def result(self):
        return self.config.cross_get(self.manager.RESULT_PATH, {})

    def save(self):
        self.config.cross_set(TRAINING_PATH, deepcopy(self.saved))

    def delay(self, message):
        logger.warning(message)
        self.save()
        self.config.task_delay(minute=max(1, interval(self.config)))
        raise ScriptEnd(message)

    def inspect(self, fleet, slots):
        self.saved['reading'] = dict(fleet=fleet, slots=list(slots))
        self.save()
        readings = self.reader.read(fleet, slots, self.targets)
        self.saved.pop('reading', None)
        known = {}
        for slot in slots:
            ship = ships(self.result, fleet).get(slot)
            if ship is None:
                continue
            name = identity(ship, self.result)
            reading = readings.get(slot, {})
            if name is None or any(type(reading.get(metric)) is not int for metric in self.targets):
                self.delay(f'第{fleet}舰队{slot}位培养身份或数据未确认，不记录完成')
            observe(self.saved, name, reading, self.targets)
            known[slot] = name
        self.save()
        return known

    def recover(self):
        """中断前已落盘的互换必须按双方身份核实，不盲目重复。"""
        for key, job in list(self.saved['pending'].items()):
            target = identity(ships(self.result, job['fleet']).get(job['slot']), self.result)
            source = identity(ships(self.result, job['source']).get(job['slot']), self.result)
            if (target, source) == (job['incoming'], job['outgoing']):
                del self.saved['pending'][key]
            elif ((target, source) == (job['outgoing'], job['incoming'])
                  and job.get('targets', self.targets) != self.targets):
                del self.saved['pending'][key]
            elif (target, source) != (job['outgoing'], job['incoming']):
                raise FleetManagementError('培养互换中断后的双方舰位未确认，保留记录，不能重复换船')
        self.save()

    def prepare(self, fleet):
        snapshots = self.manager.prepare_rotation(fleet)
        self.config.cross_set_many(calibration_changes(self.config.data, snapshots, now()))
        self.runner.campaign.emotion.calibrate_selected(snapshots)
        self.manager.finish_rotation()
        return snapshots

    def process(self, *, fresh=False):
        if not self.targets or not self.selected or (not due(self.config, self.context) and not self.saved['pending']):
            return False
        self.recover()
        mood = self.config.cross_get(ROTATION_PATH + '.Enabled', False) is True
        for role, fleet in self.selected.items():
            rule = self.rules[str(fleet)]
            if mood and not fresh:
                self.prepare(fleet)
            current = self.inspect(fleet, rule['slots'])
            ready = [slot for slot, name in current.items()
                     if excluded(self.saved['records'][name], self.targets)]
            jobs = []
            for slot in ready:
                key = f'{fleet}:{slot}'
                job = self.saved['pending'].get(key)
                if job is None:
                    for source in rule['sources']:
                        if not mood and source not in self.inventory_fleets:
                            self.reader.inventory([source])
                            self.inventory_fleets.add(source)
                        candidate = ships(self.result, source).get(slot)
                        name = identity(candidate, self.result)
                        if name is None or excluded(self.saved['records'].get(name, {}), self.targets):
                            continue
                        # 候补也实读，避免换入尚未登记但已达到全部目标的舰船。
                        names = self.inspect(source, [slot])
                        if slot not in names or excluded(self.saved['records'][name], self.targets):
                            continue
                        job = dict(fleet=fleet, slot=slot, source=source, outgoing=current[slot], incoming=name,
                                   targets=dict(self.targets))
                        break
                if job is None:
                    self.delay(f'第{fleet}舰队{slot}位已培养完成，但没有合适候补，延迟当前任务')
                jobs.append((key, job))
            if not jobs:
                continue
            self.prepare(fleet)
            self.saved['pending'].update(jobs)
            self.save()
            equipment = None
            if self.config.cross_get(EQUIPMENT_PATH + '.Enabled', False) is True:
                equipment = FleetRotationEquipment(self.manager)
                equipment.clear_outgoing(fleet, [job['slot'] for _, job in jobs])
                self.equipment_used = True
            for key, job in jobs:
                if goals(self.config) != self.targets:
                    break
                snapshots = self.manager.rotate_slot(job['source'], job['slot'], fleet)
                self.config.cross_set_many(calibration_changes(self.config.data, snapshots, now()))
                self.runner.campaign.emotion.calibrate_selected(snapshots)
                actual = identity(ships(self.result, fleet).get(job['slot']), self.result)
                if actual != job['incoming']:
                    raise FleetManagementError('培养换船后的身份未确认，保留待确认记录')
                del self.saved['pending'][key]
                self.save()
            self.manager.finish_rotation()
            if equipment is not None:
                equipment.apply_pending()
            if goals(self.config) == self.targets:
                self.inspect(fleet, [job['slot'] for _, job in jobs])
        self.context['training_checked_at'] = now()
        self.context['training_goals'] = dict(self.targets)
        self.context['training_rules'] = deepcopy(self.config.cross_get(ROTATION_PATH + '.Rules', '{}'))
        self.context['training_fleets'] = active_fleets(self.config)
        return True

    def filter_sources(self, order, rule, fleet):
        """心情临时轮换也不能把已完成培养的舰船选回来。"""
        for source in order:
            names = self.inspect(source, rule['slots'])
            if all(slot in names and not excluded(self.saved['records'][names[slot]], self.targets)
                   for slot in rule['slots']):
                self.manager.finish_rotation()
                self.manager.fleet_enter(fleet)
                return [source]
        self.manager.finish_rotation()
        self.manager.fleet_enter(fleet)
        return []


def recover_reading(runner, mode):
    """在选关导航之前退出已登记的读取页面，避免好感弹窗被当作未知界面。

    Pages:
        in: 中断的好感/角色详情，或原页面
        out: 已知读取页面返回编队；其他页面不操作
    """
    config = runner.campaign.config
    saved = config.cross_get(TRAINING_PATH, {})
    if (isinstance(saved, dict) and saved.get('reading')
            and supported(config.task.command, mode, runner.folder, runner.stage)):
        manager = FleetManagement(config=config, device=runner.device)
        FleetTrainingReader(manager).recover_page()


def training_only(runner, battle, mode):
    """仅培养调度时只进详情实读；达标才复用同号互换及心情校正。

    Pages:
        in: 普通选关页
        out: 原选关页，或按原机制延迟任务
    """
    from module.campaign.fleet_rotation import _required
    from module.campaign.fleet_rotation_emotion import RotationEmotion
    config = runner.campaign.config
    context = runner.__dict__.setdefault('_fleet_rotation_context', {})
    if context.get('task') != config.task.command:
        context.clear()
        context['task'] = config.task.command
    if not due(config, context) and not state(config)['pending']:
        return False
    rules = parse_rules(config.cross_get(ROTATION_PATH + '.Rules', '{}'))
    selected = {role: actual for role, actual in active_fleets(config).items()
                if rules.get(str(actual), {}).get('slots') and rules[str(actual)].get('sources')
                and _required(runner.campaign.emotion, role, battle)[1] is not None}
    if not selected:
        return False
    book = runner.campaign.emotion.map_is_2x_book
    runner.campaign.emotion = RotationEmotion(config, context, rules, selected)
    runner.campaign.emotion.map_is_2x_book = book
    manager = FleetManagement(config=config, device=runner.device)
    session = TrainingSession(runner, manager, context, rules, selected)
    if config.cross_get(EQUIPMENT_PATH + '.Enabled', False) and config.cross_get(EQUIPMENT_PATH + '.State', {}):
        equipment = FleetRotationEquipment(manager)
        equipment.recover_page()
    if session.saved.get('reading'):
        session.reader.recover_page()
    # 确认后、保存前中断时两边旧记录都可能过时，恢复前必须实读双方。
    recovery_fleets = {job[side] for job in session.saved['pending'].values() for side in ('fleet', 'source')}
    session.reader.inventory(set(selected.values()) | recovery_fleets)
    if config.cross_get(EQUIPMENT_PATH + '.Enabled', False) and config.cross_get(EQUIPMENT_PATH + '.State', {}):
        FleetRotationEquipment(manager).apply_pending()
    session.process(fresh=True)
    manager.finish_rotation()
    manager._commit_formation()
    config.update()
    runner.config.update()
    runner.campaign.emotion = RotationEmotion(config, context, rules, selected)
    runner.campaign.emotion.map_is_2x_book = book
    runner.campaign.ensure_campaign_ui(name=runner.stage, mode=mode, skip_first_screenshot=False)
    runner.campaign.map_get_info()
    return True


def after_battle_due(runner, mode):
    from module.campaign.fleet_rotation import _required
    config = runner.campaign.config
    if (not supported(config.task.command, mode, runner.folder, runner.stage)
            or config.Campaign_Mode != 'normal' or not goals(config)):
        return False
    rules = parse_rules(config.cross_get(ROTATION_PATH + '.Rules', '{}'))
    selected = any(rules.get(str(actual), {}).get('slots') and rules[str(actual)].get('sources')
                   and _required(runner.campaign.emotion, role, runner.campaign._map_battle)[1] is not None
                   for role, actual in active_fleets(config).items())
    return selected and due(config, runner.__dict__.get('_fleet_rotation_context', {}))
