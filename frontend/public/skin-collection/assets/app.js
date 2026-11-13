
        // Keep unreadable data available for recovery instead of aborting page startup.
        const storageRecovery = new Map();
        function notifySaveState(state, message = '') {
            window.trackerSaveState = { state, message, updatedAt: new Date().toISOString() };
            if (typeof window.onTrackerSaveState === 'function') {
                try { window.onTrackerSaveState(state, message); } catch (error) { console.error('更新保存状态失败:', error); }
            }
        }

        function readStoredArray(key) {
            let raw = null;
            try {
                raw = localStorage.getItem(key);
                if (raw === null) return [];
                const value = JSON.parse(raw);
                if (!Array.isArray(value)) throw new Error('数据必须是数组');
                if ((key === 'azurLaneCharacters' || key === 'azurLaneSkins') && value.some(record =>
                    !record || typeof record !== 'object' || typeof record.name !== 'string' ||
                    typeof record.id !== 'string' || !/^[A-Za-z0-9_-]+$/.test(record.id) ||
                    (record.acquisition !== undefined && record.acquisition !== null && typeof record.acquisition !== 'string')
                )) throw new Error('记录字段格式不正确');
                if (key === 'azurLaneActivityLog' && value.some(record => !record || typeof record !== 'object' || Array.isArray(record) ||
                    ['type', 'target', 'name', 'characterName', 'oldName', 'logMessage', 'timestamp'].some(field =>
                        record[field] !== undefined && record[field] !== null && typeof record[field] !== 'string'
                    )
                )) {
                    throw new Error('活动记录格式不正确');
                }
                return value;
            } catch (error) {
                if (raw !== null) storageRecovery.set(key, raw);
                console.error('读取本地数据失败：' + key, error);
                notifySaveState('error', '部分本地数据无法读取，原始内容已保留。请导入最近的备份恢复。');
                return [];
            }
        }

        function preserveRecoveryData() {
            for (const [key, raw] of storageRecovery) {
                localStorage.setItem(key + 'Recovery', raw);
                storageRecovery.delete(key);
            }
        }

        // Roll back all touched keys when any write fails, including quota errors.
        function writeStorageEntries(entries) {
            preserveRecoveryData();
            const previous = entries.map(([key]) => [key, localStorage.getItem(key)]);
            try {
                for (const [key, value] of entries) {
                    if (value === null) localStorage.removeItem(key);
                    else localStorage.setItem(key, value);
                }
            } catch (error) {
                // Free the space used by the attempted write before restoring larger old values.
                for (const [key] of previous) {
                    try { localStorage.removeItem(key); } catch (_) { /* Read-only storage will also reject rollback. */ }
                }
                for (const [key, value] of previous) {
                    try {
                        if (value === null) localStorage.removeItem(key);
                        else localStorage.setItem(key, value);
                    } catch (restoreError) {
                        console.error('恢复本地存储失败：' + key, restoreError);
                    }
                }
                throw error;
            }
        }

        function storageErrorMessage(error) {
            return error.name === 'QuotaExceededError'
                ? '本地空间不足，修改尚未保存。请先导出备份，再清理不需要的撤销记录。'
                : '本地保存失败，修改尚未保存。请导出备份：' + error.message;
        }

        let characters = readStoredArray('azurLaneCharacters');
        let skins = readStoredArray('azurLaneSkins');
        let skinsByCharacter = new Map();
        let skinsIndexDirty = true;
        let currentCharacterId = null;
        let activityLog = readStoredArray('azurLaneActivityLog');
        let undoHistory = readStoredArray('azurLaneUndoHistory').filter(isValidHistoryState);
        let redoHistory = readStoredArray('azurLaneRedoHistory').filter(isValidHistoryState);

        function isValidHistoryState(state) {
            try {
                validateImportedData(state);
                return true;
            } catch (_) {
                return false;
            }
        }
        
        let currentAcquisitionFilter = '';
        let currentCharacterAcquisitionFilter = '';
        let currentRarityFilter = '';
        let hideUnavailable = false;

        // 防抖保存定时器
        let _saveDataTimer = null;
        // 保存失败后定时器已经结束，但未落盘的用户编辑仍必须保留。
        let _dataDirty = false;
        let _saveUndoTimer = null;
        
        const defaultIconUrl = 'assets/anchor.svg';
        
        const tagColorMap = {
            '建造': 'bg-blue-50 text-blue-700 border-blue-200',
            '活动': 'bg-purple-50 text-purple-700 border-purple-200',
            '兑换': 'bg-orange-50 text-orange-700 border-orange-200',
            '科研': 'bg-cyan-50 text-cyan-700 border-cyan-200',
            '勋章': 'bg-yellow-50 text-yellow-700 border-yellow-200',
            '联动': 'bg-pink-50 text-pink-700 border-pink-200',
            '任务': 'bg-teal-50 text-teal-700 border-teal-200',
            '限定': 'bg-red-50 text-red-700 border-red-200',
            '常驻': 'bg-gray-50 text-gray-700 border-gray-200',
            '商店': 'bg-amber-50 text-amber-700 border-amber-200',
            '签到': 'bg-lime-50 text-lime-700 border-lime-200',
            '成就': 'bg-emerald-50 text-emerald-700 border-emerald-200',
            '招募': 'bg-indigo-50 text-indigo-700 border-indigo-200',
            '其他': 'bg-slate-50 text-slate-700 border-slate-200',
        };
        
        const fallbackTagColors = [
            'bg-blue-50 text-blue-700 border-blue-200',
            'bg-green-50 text-green-700 border-green-200',
            'bg-purple-50 text-purple-700 border-purple-200',
            'bg-yellow-50 text-yellow-700 border-yellow-200',
            'bg-pink-50 text-pink-700 border-pink-200',
            'bg-indigo-50 text-indigo-700 border-indigo-200',
            'bg-cyan-50 text-cyan-700 border-cyan-200',
            'bg-amber-50 text-amber-700 border-amber-200',
            'bg-lime-50 text-lime-700 border-lime-200',
            'bg-rose-50 text-rose-700 border-rose-200',
            'bg-violet-50 text-violet-700 border-violet-200',
            'bg-sky-50 text-sky-700 border-sky-200',
            'bg-emerald-50 text-emerald-700 border-emerald-200',
            'bg-fuchsia-50 text-fuchsia-700 border-fuchsia-200',
            'bg-teal-50 text-teal-700 border-teal-200',
            'bg-orange-50 text-orange-700 border-orange-200',
        ];

        // === 换装索引映射（性能优化：O(N×M) → O(N)） ===
        function markSkinsIndexDirty() {
            skinsIndexDirty = true;
        }

        function buildSkinsIndex() {
            skinsByCharacter = new Map();
            for (let i = 0; i < skins.length; i++) {
                const skin = skins[i];
                let arr = skinsByCharacter.get(skin.characterId);
                if (!arr) {
                    arr = [];
                    skinsByCharacter.set(skin.characterId, arr);
                }
                arr.push(skin);
            }
            skinsIndexDirty = false;
        }

        function getSkinsByCharacter(characterId) {
            if (skinsIndexDirty) {
                buildSkinsIndex();
            }
            return skinsByCharacter.get(characterId) || [];
        }

        // 换装拥有状态独立于角色收集数量；未录入换装也属于未拥有。
        function hasNoOwnedSkins(character) {
            return !getSkinsByCharacter(character.id).some(skin => skin.owned);
        }

        // ==================== 拼音搜索工具函数 ====================

        const pinyinCache = new Map();

        function getPinyinData(text) {
            if (!text) return null;
            if (pinyinCache.has(text)) return pinyinCache.get(text);

            const chars = Array.from(text);
            let fullPinyinArr, initialsArr;

            if (typeof pinyinPro !== 'undefined') {
                fullPinyinArr = pinyinPro.pinyin(text, { toneType: 'none', type: 'array' }).map(function(p) { return p.toLowerCase(); });
                initialsArr = pinyinPro.pinyin(text, { pattern: 'first', toneType: 'none', type: 'array' }).map(function(p) { return p.toLowerCase(); });
            } else {
                fullPinyinArr = chars.map(function(c) { return c.toLowerCase(); });
                initialsArr = chars.map(function(c) { return c.toLowerCase(); });
            }

            const data = {
                chars: chars,
                fullPinyinArr: fullPinyinArr,
                initialsArr: initialsArr,
                fullPinyinStr: fullPinyinArr.join(''),
                initialsStr: initialsArr.join('')
            };
            pinyinCache.set(text, data);
            return data;
        }

        // 返回匹配分数（越高越匹配，-1表示不匹配）
        function pinyinMatchScore(query, text) {
            if (!query || !text) return -1;

            var q = query.toLowerCase();
            var t = text.toLowerCase();

            // 1. 直接子串匹配（中文对中文、英文对英文）
            var directIdx = t.indexOf(q);
            if (directIdx !== -1) return 1000 - directIdx;

            var pd = getPinyinData(text);
            if (!pd) return -1;

            // 2. 全拼匹配（如 "zhongguo" 匹配 "中国"）
            var pinyinIdx = pd.fullPinyinStr.indexOf(q);
            if (pinyinIdx !== -1) return 800 - pinyinIdx;

            // 3. 首字母匹配（如 "zg" 匹配 "中国"）
            var initIdx = pd.initialsStr.indexOf(q);
            if (initIdx !== -1) return 600 - initIdx;

            // 4. 混合匹配（如 "中guo" 匹配 "中国"）— 仅当查询同时包含中文和拉丁字母时触发
            var hasChinese = /[\u4e00-\u9fa5]/.test(q);
            var hasLatin = /[a-z]/.test(q);
            if (hasChinese && hasLatin) {
                if (mixedPinyinMatch(q, pd)) return 400;
            }

            return -1;
        }

        // 混合拼音匹配：递归尝试每种可能的匹配方式
        function mixedPinyinMatch(query, pd) {
            for (var start = 0; start < pd.chars.length; start++) {
                if (tryMixedMatch(query, 0, pd, start, 0)) return true;
            }
            return false;
        }

        function tryMixedMatch(query, qPos, pd, charPos, pinyinOffset) {
            if (qPos >= query.length) return true;
            if (charPos >= pd.chars.length) return false;

            var qChar = query[qPos];
            var targetChar = pd.chars[charPos].toLowerCase();
            var fullPinyin = pd.fullPinyinArr[charPos] || '';
            var initial = pd.initialsArr[charPos] || '';

            // 非中文字符（拼音与字符本身相同），直接匹配
            if (fullPinyin === targetChar || !fullPinyin) {
                if (qChar === targetChar) {
                    return tryMixedMatch(query, qPos + 1, pd, charPos + 1, 0);
                }
                return false;
            }

            // 正在匹配当前字符的拼音中间部分
            if (pinyinOffset > 0) {
                if (pinyinOffset < fullPinyin.length && qChar === fullPinyin[pinyinOffset]) {
                    if (tryMixedMatch(query, qPos + 1, pd, charPos, pinyinOffset + 1)) return true;
                    if (pinyinOffset + 1 === fullPinyin.length) {
                        return tryMixedMatch(query, qPos + 1, pd, charPos + 1, 0);
                    }
                }
                return false;
            }

            // 尝试中文字符直接匹配
            if (qChar === targetChar) {
                if (tryMixedMatch(query, qPos + 1, pd, charPos + 1, 0)) return true;
            }

            // 尝试全拼匹配
            if (qChar === fullPinyin[0]) {
                if (fullPinyin.length === 1) {
                    if (tryMixedMatch(query, qPos + 1, pd, charPos + 1, 0)) return true;
                } else {
                    if (tryMixedMatch(query, qPos + 1, pd, charPos, 1)) return true;
                }
            }

            // 尝试首字母匹配
            if (qChar === initial) {
                if (tryMixedMatch(query, qPos + 1, pd, charPos + 1, 0)) return true;
            }

            return false;
        }

        // 对多个字段取最高匹配分数
        function bestMatchScore(query, texts) {
            var best = -1;
            for (var i = 0; i < texts.length; i++) {
                if (texts[i]) {
                    var score = pinyinMatchScore(query, texts[i]);
                    if (score > best) best = score;
                }
            }
            return best;
        }

        // ==================== 拼音搜索工具函数结束 ====================

        function getTagColorClass(tag) {
            if (tagColorMap[tag]) {
                return tagColorMap[tag];
            }
            
            const hash = tag.split('').reduce((acc, char) => acc + char.charCodeAt(0), 0);
            return fallbackTagColors[hash % fallbackTagColors.length];
        }
        
        function initUndoState() {
            // Each actual mutation already records its previous state.
            // Do not create a no-op undo item or write storage during startup.
            updateUndoButton();
        }
        
        function initCustomIcon() {
            const iconElement = document.getElementById('custom-icon');
            try {
                const savedIcon = localStorage.getItem('azurLaneCustomIcon');
                iconElement.src = savedIcon || defaultIconUrl;
            } catch (error) {
                iconElement.src = defaultIconUrl;
            }
            iconElement.onerror = () => {
                iconElement.onerror = null;
                iconElement.src = defaultIconUrl;
            };
        }
        
        function uploadCustomIcon(event) {
            const file = event.target.files[0];
            if (file) {
                const reader = new FileReader();
                reader.onload = function(e) {
                    const iconDataUrl = e.target.result;
                    document.getElementById('custom-icon').src = iconDataUrl;
                    localStorage.setItem('azurLaneCustomIcon', iconDataUrl);
                };
                reader.readAsDataURL(file);
            }
        }
        
        const characterList = document.getElementById('character-list');
        const characterDetail = document.getElementById('character-detail');
        const incompleteList = document.getElementById('incomplete-list');
        const searchInput = document.getElementById('search-input');
        
        const characterModal = document.getElementById('character-modal');
        const characterForm = document.getElementById('character-form');
        const skinModal = document.getElementById('skin-modal');
        const skinForm = document.getElementById('skin-form');
        const importModal = document.getElementById('import-modal');
        const quickAddModal = document.getElementById('quick-add-modal');
        
        document.getElementById('edit-character-btn').addEventListener('click', () => openCharacterModal(currentCharacterId));
        document.getElementById('add-skin-btn').addEventListener('click', () => openSkinModal());
        document.getElementById('delete-character-btn').addEventListener('click', () => deleteCharacter(currentCharacterId));
        document.getElementById('export-btn').addEventListener('click', exportData);
        document.getElementById('import-btn').addEventListener('click', () => importModal.classList.remove('hidden'));
        document.getElementById('quick-add-btn').addEventListener('click', openQuickAddModal);
        document.getElementById('undo-btn').addEventListener('click', undoLastAction);
        document.getElementById('redo-btn').addEventListener('click', redoLastAction);
        document.getElementById('cancel-character-btn').addEventListener('click', () => characterModal.classList.add('hidden'));
        document.getElementById('cancel-skin-btn').addEventListener('click', () => skinModal.classList.add('hidden'));
        document.getElementById('cancel-import-btn').addEventListener('click', () => importModal.classList.add('hidden'));
        document.getElementById('cancel-quick-add-btn').addEventListener('click', closeQuickAddModal);
        document.getElementById('confirm-import-btn').addEventListener('click', importData);
        document.getElementById('confirm-quick-add-btn').addEventListener('click', submitQuickAddRows);
        document.getElementById('add-quick-row-btn').addEventListener('click', () => addQuickAddRow());
        document.getElementById('reset-quick-rows-btn').addEventListener('click', resetQuickAddRows);
        document.getElementById('quick-add-rows').addEventListener('click', handleQuickAddRowClick);
        document.getElementById('quick-add-rows').addEventListener('input', handleQuickAddRowInput);
        document.getElementById('quick-add-rows').addEventListener('change', handleQuickAddRowInput);
        
        characterForm.addEventListener('submit', saveCharacter);
        skinForm.addEventListener('submit', saveSkin);
        
        // 自定义目标数量输入实时验证
        (function() {
            const customTargetInput = document.getElementById('character-custom-target');
            const customTargetError = document.getElementById('custom-target-error');
            if (customTargetInput && customTargetError) {
                customTargetInput.addEventListener('input', function() {
                    const rawValue = this.value.trim();
                    if (rawValue === '') {
                        customTargetError.classList.add('hidden');
                        return;
                    }
                    const numValue = Number(rawValue);
                    if (!Number.isInteger(numValue)) {
                        customTargetError.textContent = '请输入整数';
                        customTargetError.classList.remove('hidden');
                    } else if (numValue < 0) {
                        customTargetError.textContent = '目标数量不能小于 0';
                        customTargetError.classList.remove('hidden');
                    } else if (numValue > 999) {
                        customTargetError.textContent = '目标数量不能超过 999';
                        customTargetError.classList.remove('hidden');
                    } else {
                        // 允许收集数量超过目标数量，因此不再校验目标数量与已收集数量的关系
                        customTargetError.classList.add('hidden');
                    }
                });
            }
        })();
        
        // 搜索输入处理
        // 搜索防抖（150ms），确保拼音搜索流畅
        const _searchDebounceTimers = new Map();
        function debouncedSearch(callback) {
            clearTimeout(_searchDebounceTimers.get(callback));
            _searchDebounceTimers.set(callback, setTimeout(() => {
                _searchDebounceTimers.delete(callback);
                callback();
            }, 150));
        }

        searchInput.addEventListener('input', function() {
            const clearBtn = document.getElementById('clear-search-btn');
            if (this.value.trim() !== '') {
                clearBtn.classList.remove('hidden');
            } else {
                clearBtn.classList.add('hidden');
            }
            debouncedSearch(filterCharacters);
        });
        document.getElementById('incomplete-search').addEventListener('input', function() {
            const clearBtn = document.getElementById('clear-incomplete-search-btn');
            if (this.value.trim() !== '') {
                clearBtn.classList.remove('hidden');
            } else {
                clearBtn.classList.add('hidden');
            }
            debouncedSearch(filterIncompleteCharacters);
        });
        
        document.getElementById('clear-incomplete-search-btn').addEventListener('click', function() {
            const incompleteSearchInput = document.getElementById('incomplete-search');
            incompleteSearchInput.value = '';
            filterIncompleteCharacters();
            this.classList.add('hidden');
        });

        // 隐藏/显示无法获取角色按钮
        document.getElementById('hide-unavailable-btn').addEventListener('click', function() {
            hideUnavailable = !hideUnavailable;
            const btn = document.getElementById('hide-unavailable-btn');
            const label = document.getElementById('hide-unavailable-label');
            if (hideUnavailable) {
                btn.classList.remove('btn-outline');
                btn.classList.add('btn-secondary');
                label.textContent = '显示无法获取角色';
            } else {
                btn.classList.remove('btn-secondary');
                btn.classList.add('btn-outline');
                label.textContent = '隐藏无法获取角色';
            }
            filterIncompleteCharacters();
        });
        
        // 清空搜索按钮
        document.getElementById('clear-search-btn').addEventListener('click', function() {
            searchInput.value = '';
            filterCharacters();
            this.classList.add('hidden');
        });
        
        document.getElementById('rarity-filter-all').addEventListener('click', function() {
            setRarityFilter('');
        });
        document.getElementById('rarity-filter-ultra_rare').addEventListener('click', function() {
            setRarityFilter('ultra_rare');
        });
        document.getElementById('rarity-filter-super_rare').addEventListener('click', function() {
            setRarityFilter('super_rare');
        });
        document.getElementById('rarity-filter-elite').addEventListener('click', function() {
            setRarityFilter('elite');
        });
        document.getElementById('rarity-filter-rare').addEventListener('click', function() {
            setRarityFilter('rare');
        });
        document.getElementById('rarity-filter-common').addEventListener('click', function() {
            setRarityFilter('common');
        });
        
        function setRarityFilter(rarity) {
            currentRarityFilter = rarity;
            
            document.querySelectorAll('[id^="rarity-filter-"]').forEach(btn => {
                btn.classList.remove('ring-2', 'ring-offset-2');
                if (btn.id === 'rarity-filter-all') {
                    btn.classList.toggle('active', rarity === '');
                }
            });
            
            const activeBtn = document.getElementById(`rarity-filter-${rarity || 'all'}`);
            if (activeBtn) {
                activeBtn.classList.add('ring-2', 'ring-offset-2');
            }
            
            renderIncompleteList(document.getElementById('incomplete-search').value.trim());
        }
        
        initUndoState();
        renderAll();
        renderRecentActivity();
        renderAcquisitionChart();
        updateUndoButton();
        updateRedoButton();
        initCustomIcon();
        updateUncollectedValueDisplay();
        
        // requestAnimationFrame 批量渲染（性能优化：合并多次渲染为单次 rAF 调用）
        let _renderScheduled = false;

        function scheduleRender() {
            if (_renderScheduled) return;
            _renderScheduled = true;
            requestAnimationFrame(() => {
                _renderScheduled = false;
                renderAll();
                renderRecentActivity();
                renderAcquisitionChart();
            });
        }

        function renderAll() {
            const currentSearchFilter = searchInput.value.trim();
            const currentIncompleteFilter = document.getElementById('incomplete-search').value.trim();
            
            renderCharacterList(currentSearchFilter);
            renderIncompleteList(currentIncompleteFilter);
            
            if (currentCharacterId && characters.some(character => character.id === currentCharacterId)) {
                showCharacterDetail(currentCharacterId, false);
            } else {
                currentCharacterId = null;
                characterDetail.classList.add('hidden');
            }
            updateUncollectedValueDisplay();
        }
        
        function toggleActivityDropdown() {
            const dropdown = document.getElementById('recent-activity-dropdown');
            dropdown.classList.toggle('hidden');
        }
        
        document.addEventListener('click', function(event) {
            const dropdown = document.getElementById('recent-activity-dropdown');
            const activityElement = document.getElementById('recent-activity');
            
            if (!dropdown.contains(event.target) && !activityElement.contains(event.target)) {
                dropdown.classList.add('hidden');
            }
        });
        
        function renderCharacterList(filter = '') {
            characterList.innerHTML = '';
            const searchScores = new Map();
            
            let filteredCharacters = characters.filter(char => {
                if (currentCharacterAcquisitionFilter) {
                    if (currentCharacterAcquisitionFilter === '未拥有获取方式') {
                        const acquisition = char.acquisition || '';
                        if (acquisition.trim()) {
                            return false;
                        }
                    } else if (currentCharacterAcquisitionFilter === '未拥有换装角色') {
                        if (!hasNoOwnedSkins(char)) return false;
                    } else if (currentCharacterAcquisitionFilter === '拥有未使用的换装') {
                        const characterSkins = getSkinsByCharacter(char.id);
                        const ownedSkinsCount = characterSkins.filter(skin => skin.owned).length;
                        const collectedCount = getEffectiveCollected(char);
                        if (ownedSkinsCount <= collectedCount) {
                            return false;
                        }
                    } else {
                        const characterAcquisition = char.acquisition || '未设置';
                        const acquisitionMethods = characterAcquisition.split('、').map(method => method.trim()).filter(method => method);
                        if (!acquisitionMethods.includes(currentCharacterAcquisitionFilter)) {
                            return false;
                        }
                    }
                    
                    const characterSkins = getSkinsByCharacter(char.id);
                    const totalCount = characterSkins.length;
                    // 换装拥有状态筛选不受角色是否完成收集影响。
                    if (currentCharacterAcquisitionFilter !== '未拥有换装角色' && totalCount >= 0 && isCharacterCompleted(char)) {
                        return false;
                    }
                }
                
                if (filter) {
                    var texts = [char.name, char.acquisition || ''];
                    var characterSkinsForSearch = getSkinsByCharacter(char.id);
                    for (var si = 0; si < characterSkinsForSearch.length; si++) {
                        texts.push(characterSkinsForSearch[si].name);
                    }
                    var score = bestMatchScore(filter, texts);
                    if (score < 0) {
                        return false;
                    }
                    searchScores.set(char.id, score);
                } else {
                    searchScores.set(char.id, 0);
                }
                
                return true;
            });
            
            filteredCharacters.sort((a, b) => {
                // 有搜索词时，按匹配分数降序排列
                if (filter) {
                    var aScore = searchScores.get(a.id) || 0;
                    var bScore = searchScores.get(b.id) || 0;
                    if (aScore !== bScore) {
                        return bScore - aScore;
                    }
                }

                const dateA = a.updatedAt ? new Date(a.updatedAt) : new Date(0);
                const dateB = b.updatedAt ? new Date(b.updatedAt) : new Date(0);
                return dateB - dateA;
            });
            
            if (filteredCharacters.length === 0) {
                characterList.innerHTML = '<div class="flex flex-col items-center justify-center h-full text-gray-500"><i class="fa fa-search-minus text-4xl mb-3 opacity-50"></i><p>没有找到角色</p></div>';
                return;
            }
            
            filteredCharacters.forEach(character => {
                const characterSkins = getSkinsByCharacter(character.id);
                const totalCount = characterSkins.length;
                const collectedCount = characterSkins.filter(skin => skin.owned).length;
                const characterCollected = getEffectiveCollected(character);
                const targetCount = getTargetCount(character);
                
                const rarityClass = `bg-rarity-${character.rarity}`;
                
                const characterElement = document.createElement('div');
                const isSelected = currentCharacterId === character.id;
                characterElement.className = `flex flex-col p-3 border-b border-gray-100 cursor-pointer transition-all duration-200 ${isSelected ? 'bg-blue-100' : 'hover:bg-blue-50'}`;
                characterElement.dataset.id = character.id;
                // 三种完成状态：绿色（独立完成）、黄色（仅靠可共享换装达到目标）、默认（未完成）
                const isFullyCompleted = totalCount >= 0 && isCharacterCompleted(character);
                const isSharedCompleted = !isFullyCompleted && totalCount >= 0 && isCharacterCompletedViaShareable(character);
                let collectTagClass, collectIcon, collectIconClass, nameColorClass;
                if (isFullyCompleted) {
                    collectTagClass = 'bg-green-100 text-green-700';
                    collectIcon = 'fa-star';
                    collectIconClass = 'text-yellow-500';
                    nameColorClass = 'text-green-600';
                } else if (isSharedCompleted) {
                    collectTagClass = 'bg-yellow-100 text-yellow-800';
                    collectIcon = 'fa-star-half-o';
                    collectIconClass = 'text-amber-500';
                    nameColorClass = 'text-yellow-600';
                } else {
                    collectTagClass = 'bg-blue-100 text-blue-700';
                    collectIcon = 'fa-star-o';
                    collectIconClass = 'text-gray-400';
                    nameColorClass = '';
                }

                characterElement.innerHTML = `
                    <div class="flex flex-col flex-1">
                        <div class="flex items-center justify-between">
                            <div class="flex items-center">
                                <span class="badge badge-rarity ${rarityClass} mr-2">${getRarityText(character.rarity)}</span>
                                <span class="font-medium ${nameColorClass}">${escapeHtml(character.name)}</span>
                                <span class="ml-2 text-sm px-3 py-1 rounded-full ${collectTagClass} flex items-center font-semibold">
                                    <i class="fa ${collectIcon} ${collectIconClass} mr-1"></i>
                                    ${characterCollected}/${targetCount}
                                </span>
                            </div>
                            <div class="flex items-center">
                                <span class="flex items-center text-sm ${collectedCount >= targetCount && totalCount >= 0 ? 'text-green-600' : 'text-gray-500'}">
                                    <i class="fa fa-tshirt mr-1.5 text-accent"></i>
                                    已拥有${collectedCount}件换装
                                </span>
                            </div>
                        </div>
                        <div class="flex flex-wrap items-center justify-between gap-2 mt-2">
                            <div class="flex flex-wrap gap-2" id="character-tags-${character.id}"></div>
                            <div class="flex flex-wrap gap-1 justify-end flex-shrink-0" id="character-status-tags-${character.id}"></div>
                        </div>
                    </div>
                `;
                
                // 渲染标签
                const tagsContainer = characterElement.querySelector(`#character-tags-${character.id}`);
                const acquisition = character.acquisition || '';
                const tags = acquisition ? acquisition.split('、').map(tag => tag.trim()).filter(tag => tag) : [];
                
                tags.forEach(tag => {
                    const tagElement = document.createElement('span');
                    const styleClass = getTagColorClass(tag);
                    tagElement.className = `text-xs px-2.5 py-1 rounded-full border ${styleClass} truncate max-w-[120px]`;
                    tagElement.textContent = tag;
                    tagElement.title = tag;
                    tagsContainer.appendChild(tagElement);
                });
                
                // 添加特殊状态标签（与获取方式标签同一行，位于最右侧）
                const statusTagsContainer = characterElement.querySelector(`#character-status-tags-${character.id}`);
                const allSkinsNotOwned = hasNoOwnedSkins(character);
                const ownedSkinsCount = characterSkins.filter(skin => skin.owned).length;
                const hasUnusedSkins = ownedSkinsCount > characterCollected;
                
                if (allSkinsNotOwned) {
                    const statusTag = document.createElement('span');
                    statusTag.className = 'text-xs px-2.5 py-1 rounded-full bg-gray-100 text-gray-700 border border-gray-300';
                    statusTag.textContent = '未拥有换装角色';
                    statusTag.title = '该角色未拥有任何换装（含未录入换装）';
                    statusTagsContainer.appendChild(statusTag);
                }
                
                if (hasUnusedSkins) {
                    const statusTag = document.createElement('span');
                    statusTag.className = 'text-xs px-2.5 py-1 rounded-full bg-orange-100 text-orange-700 border border-orange-300';
                    statusTag.textContent = '拥有未使用的换装';
                    statusTag.title = `已拥有${ownedSkinsCount}件换装，但只收集了${characterCollected}件`;
                    statusTagsContainer.appendChild(statusTag);
                }
                
                characterElement.addEventListener('click', () => showCharacterDetail(character.id, false));
                characterList.appendChild(characterElement);
            });
        }
        
        function showCharacterDetail(characterId, shouldScroll = false) {
            const character = characters.find(char => char.id === characterId);
            if (!character) return;

            const previousCharacterId = currentCharacterId;
            currentCharacterId = characterId;

            // 轻量级选中态切换：仅更新 CSS class，不重新渲染整个列表
            if (previousCharacterId !== characterId) {
                const children = characterList.children;
                for (let i = 0; i < children.length; i++) {
                    const el = children[i];
                    const id = el.dataset.id;
                    if (id === previousCharacterId) {
                        el.classList.remove('bg-blue-100');
                        el.classList.add('hover:bg-blue-50');
                    } else if (id === characterId) {
                        el.classList.add('bg-blue-100');
                        el.classList.remove('hover:bg-blue-50');
                    }
                }
            }
            
            const characterDetail = document.getElementById('character-detail');
            
            const detailNameElement = document.getElementById('detail-name');
            detailNameElement.textContent = character.name;
            detailNameElement.className = 'text-xl font-bold text-primary cursor-pointer hover:text-blue-700 transition-colors';
            detailNameElement.title = '点击复制角色名称';
            
            detailNameElement.onclick = function() {
                const textarea = document.createElement('textarea');
                textarea.value = character.name;
                textarea.style.position = 'fixed';
                textarea.style.opacity = '0';
                document.body.appendChild(textarea);
                
                try {
                    textarea.select();
                    textarea.setSelectionRange(0, 99999);
                    document.execCommand('copy');
                    
                    const originalText = this.textContent;
                    const originalClass = this.className;
                    
                    this.textContent = '✓ 已复制';
                    this.className = 'text-xl font-bold text-green-600';
                    
                    setTimeout(() => {
                        this.textContent = originalText;
                        this.className = originalClass;
                    }, 1500);
                } catch (err) {
                    console.error('复制失败:', err);
                } finally {
                    document.body.removeChild(textarea);
                }
            };
            
            document.getElementById('detail-rarity').textContent = getRarityText(character.rarity);
            document.getElementById('detail-rarity').className = `badge badge-rarity bg-rarity-${character.rarity}`;
            
            const totalSkins = getSkinsByCharacter(characterId).length;
            const collectedCount = getEffectiveCollected(character);
            const targetCount = getTargetCount(character);
            
            document.getElementById('detail-acquisition').innerHTML = `
                <div class="flex items-center space-x-2">
                    <span>获取方式:</span>
                    <input type="text" 
                           id="acquisition-input" 
                           value="${escapeHtml(character.acquisition || '')}" 
                           class="px-3 py-1.5 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 flex-1"
                           placeholder="输入获取方式...">
                    <button id="save-acquisition-btn" 
                            class="px-3 py-1.5 bg-blue-600 text-white text-sm rounded-lg hover:bg-blue-700 transition-colors">
                        保存
                    </button>
                </div>
            `;
            
            // 保存获取方式的函数
            const saveAcquisition = () => {
                const newAcquisition = document.getElementById('acquisition-input').value.trim();
                if (newAcquisition !== character.acquisition) {
                    saveUndoState();
                    
                    const oldValue = character.acquisition;
                    character.acquisition = newAcquisition;
                    character.updatedAt = new Date().toISOString();
                    
                    addActivityLog({
                        type: 'update',
                        target: 'character',
                        id: character.id,
                        name: character.name,
                        logMessage: `更新获取方式从 "${oldValue || '未设置'}" 到 "${newAcquisition || '未设置'}"`,
                        timestamp: character.updatedAt
                    });
                    
                    saveData();
                    scheduleRender();
                }
            };
            
            document.getElementById('save-acquisition-btn').onclick = saveAcquisition;
            
            document.getElementById('acquisition-input').addEventListener('keydown', function(e) {
                if (e.key === 'Enter') {
                    e.preventDefault();
                    saveAcquisition();
                }
            });
            
            let progressContainer = document.getElementById('collection-progress-container');
            if (!progressContainer) {
                progressContainer = document.createElement('div');
                progressContainer.id = 'collection-progress-container';
                progressContainer.className = 'flex items-center';
                const characterInfoRow = document.querySelector('#character-detail .flex.items-center.justify-between');
                characterInfoRow.appendChild(progressContainer);
            }
            
            const progressHTML = `
                <span class="mr-2 text-gray-600">收集:</span>
                <div class="flex items-center border rounded-lg overflow-hidden shadow-sm">
                    <button type="button" class="px-3 py-1.5 bg-gray-100 hover:bg-gray-200 transition-colors" 
                        data-character-id="${character.id}" data-change="-1" data-max="${targetCount}">-</button>
                    <input type="text" value="${collectedCount}" 
                        class="w-10 text-center border-x-0 focus:outline-none flex items-center justify-center h-9 font-medium" 
                        data-character-id="${character.id}" data-max="${targetCount}"
                        onkeypress="if(event.key === 'Enter') this.blur()"
                        style="text-align: center; padding: 0;">
                    <button type="button" class="px-3 py-1.5 bg-gray-100 hover:bg-gray-200 transition-colors" 
                        data-character-id="${character.id}" data-change="1" data-max="${targetCount}">+</button>
                </div>
                <span class="mx-1 text-gray-500">/</span>
                <span class="font-medium text-gray-700">${targetCount}</span>
            `;
            
            progressContainer.innerHTML = progressHTML;
            
            setTimeout(() => {
                const buttons = progressContainer.querySelectorAll('button[data-character-id]');
                buttons.forEach(button => {
                    const newButton = button.cloneNode(true);
                    button.parentNode.replaceChild(newButton, button);
                    
                    newButton.addEventListener('click', function() {
                        const characterId = this.dataset.characterId;
                        const change = parseInt(this.dataset.change, 10);
                        const max = parseInt(this.dataset.max, 10);
                        changeCollectedCount(characterId, change, max);
                    });
                });
                
                const inputs = progressContainer.querySelectorAll('input[data-character-id]');
                inputs.forEach(input => {
                    input.addEventListener('change', function() {
                        const characterId = this.dataset.characterId;
                        const value = this.value;
                        const max = parseInt(this.dataset.max, 10);
                        
                        updateCollectedCountFromInput(characterId, value, max);
                    });
                });
            }, 0);
            
            // ==================== 可共享换装角色数量 记录区域 ====================
            
            // 读取当前数值
            const shareableSkinCount = parseInt(character.shareableSkinCount, 10) || 0;
            
            // 查找或创建额外属性容器（放在换装列表标题行右侧）
            let extraStatsContainer = document.getElementById('extra-stats-container');
            if (!extraStatsContainer) {
                extraStatsContainer = document.createElement('div');
                extraStatsContainer.id = 'extra-stats-container';
                extraStatsContainer.className = 'flex items-center';
                const skinsTitleRow = document.getElementById('skins-title-row');
                if (skinsTitleRow) {
                    skinsTitleRow.appendChild(extraStatsContainer);
                }
            }

            extraStatsContainer.innerHTML = `
                <!-- 可共享换装角色数量 -->
                <div class="flex items-center bg-indigo-50 px-3 py-1.5 rounded-lg border border-indigo-200 shadow-sm">
                    <span class="mr-2 text-sm text-indigo-700 font-medium flex items-center whitespace-nowrap">
                        <i class="fa fa-share-alt mr-1"></i>可共享换装角色:
                    </span>
                    <div class="flex items-center border border-indigo-300 rounded-md overflow-hidden bg-white">
                        <button type="button" class="px-2 py-1 bg-indigo-100 hover:bg-indigo-200 text-indigo-700 transition-colors text-sm" 
                            data-character-id="${character.id}" data-field="shareableSkinCount" data-change="-1">-</button>
                        <input type="text" value="${shareableSkinCount}" 
                            class="w-10 text-center border-x-0 border-indigo-300 focus:outline-none flex items-center justify-center h-7 text-sm font-medium text-indigo-900" 
                            data-character-id="${character.id}" data-field="shareableSkinCount"
                            style="text-align: center; padding: 0;" title="点击编辑数量">
                        <button type="button" class="px-2 py-1 bg-indigo-100 hover:bg-indigo-200 text-indigo-700 transition-colors text-sm" 
                            data-character-id="${character.id}" data-field="shareableSkinCount" data-change="1">+</button>
                    </div>
                    <span class="ml-1.5 text-xs text-indigo-500 whitespace-nowrap">个</span>
                </div>
            `;
            
            // 绑定增减按钮事件
            setTimeout(() => {
                const extraButtons = extraStatsContainer.querySelectorAll('button[data-character-id]');
                extraButtons.forEach(button => {
                    const newButton = button.cloneNode(true);
                    button.parentNode.replaceChild(newButton, button);
                    
                    newButton.addEventListener('click', function() {
                        const charId = this.dataset.characterId;
                        const field = this.dataset.field;
                        const change = parseInt(this.dataset.change, 10);
                        changeExtraCount(charId, field, change);
                    });
                });
                
                const extraInputs = extraStatsContainer.querySelectorAll('input[data-character-id]');
                extraInputs.forEach(input => {
                    input.addEventListener('change', function() {
                        const charId = this.dataset.characterId;
                        const field = this.dataset.field;
                        const value = this.value;
                        
                        let numValue = parseInt(value, 10);
                        if (isNaN(numValue)) numValue = 0;
                        numValue = Math.max(0, numValue);
                        
                        updateExtraCountFromInput(charId, field, numValue.toString());
                    });
                    
                    input.addEventListener('keypress', function(e) {
                        if (e.key === 'Enter') this.blur();
                    });
                });
            }, 0);
            
            // ==================== 可共享换装角色数量 记录区域结束 ====================
            
            const skinsList = document.getElementById('skins-list');
            skinsList.innerHTML = '';
            
            let characterSkins = getSkinsByCharacter(characterId);
            
            characterSkins.sort((a, b) => {
                if (a.owned !== b.owned) {
                    return a.owned ? 1 : -1;
                }
                const dateA = a.createdAt ? new Date(a.createdAt) : new Date(0);
                const dateB = b.createdAt ? new Date(b.createdAt) : new Date(0);
                return dateB - dateA;
            });
            
            if (characterSkins.length === 0) {
                skinsList.innerHTML = '<div class="flex flex-col items-center justify-center h-32 text-gray-500"><i class="fa fa-shopping-bag text-3xl mb-2 opacity-50"></i><p>该角色暂无换装</p></div>';
            } else {
                characterSkins.forEach(skin => {
                    const skinElement = document.createElement('div');
                    skinElement.className = `flex items-center justify-between p-3 rounded-xl cursor-pointer transition-all duration-200 ${skin.owned ? 'bg-green-50 hover:bg-green-100 border border-green-200' : 'bg-gray-50 hover:bg-gray-100 border border-gray-200'}`;
                    skinElement.innerHTML = `
                        <div class="flex-1 min-w-0">
                            <h4 class="font-medium truncate">${escapeHtml(skin.name)}</h4>
                            <span class="text-sm text-gray-500 cursor-pointer hover:text-blue-600 hover:bg-blue-50 px-1 py-0.5 rounded inline-block min-w-[120px]" onclick="event.stopPropagation(); editSkinAcquisition('${skin.id}', event.target)">
                                ${escapeHtml(skin.acquisition || '点击编辑获取方式')}
                            </span>
                        </div>
                        <div class="flex items-center space-x-3">
                            <label class="inline-flex items-center cursor-pointer">
                                <input type="checkbox" class="w-5 h-5 text-blue-600 rounded focus:ring-blue-500" ${skin.owned ? 'checked' : ''} 
                                    onchange="toggleSkinOwned('${skin.id}')">
                                <span class="ml-2 text-sm">${skin.owned ? '已拥有' : '未拥有'}</span>
                            </label>
                            <button class="text-gray-400 hover:text-red-500 transition-colors" onclick="deleteSkin('${skin.id}')">
                                <i class="fa fa-trash"></i>
                            </button>
                        </div>
                    `;
                    
                    skinElement.addEventListener('click', function(event) {
                        if (event.target.closest('input, label, button, [data-editable]')) {
                            return;
                        }
                        const editArea = skinElement.querySelector('[onclick^="editSkinAcquisition"]');
                        if (editArea && (event.target === editArea || editArea.contains(event.target))) {
                            return;
                        }
                        toggleSkinOwned(skin.id);
                    });
                    
                    skinsList.appendChild(skinElement);
                });
            }
            
            characterDetail.classList.remove('hidden');
            
            if (shouldScroll) {
                characterDetail.scrollIntoView({ behavior: 'smooth' });
            }
        }
        
        function renderIncompleteList(filter = '') {
            incompleteList.innerHTML = '';
            const searchScores = new Map();
            const viewingNoOwnedSkins = currentAcquisitionFilter === '未拥有换装角色';
            document.querySelector('#incomplete-panel .filter-caption').textContent = viewingNoOwnedSkins
                ? '当前查看未拥有换装的角色，包含收集数量已达标及尚未录入换装的角色。'
                : '查看已录入换装、尚未独立完成的角色。';
            
            let incompleteCharacters = characters.filter(character => {
                if (viewingNoOwnedSkins) return hasNoOwnedSkins(character);
                const characterSkins = getSkinsByCharacter(character.id);
                if (characterSkins.length === 0) return false;
                // 仅独立完成收集的角色（绿色）才排除，黄色和未完成角色均保留
                return !isCharacterCompleted(character);
            });
            
            if (currentAcquisitionFilter && !viewingNoOwnedSkins) {
                if (currentAcquisitionFilter === '未拥有获取方式') {
                    incompleteCharacters = incompleteCharacters.filter(char => {
                        const acquisition = char.acquisition || '';
                        return !acquisition.trim();
                    });

                } else if (currentAcquisitionFilter === '拥有未使用的换装') {
                    incompleteCharacters = incompleteCharacters.filter(char => {
                        const characterSkins = getSkinsByCharacter(char.id);
                        const ownedSkinsCount = characterSkins.filter(skin => skin.owned).length;
                        const collectedCount = getEffectiveCollected(char);
                        return ownedSkinsCount > collectedCount;
                    });
                } else {
                    incompleteCharacters = incompleteCharacters.filter(char => {
                        const acquisition = char.acquisition || '未设置';
                        const acquisitionMethods = acquisition.split('、').map(method => method.trim()).filter(method => method);
                        return acquisitionMethods.includes(currentAcquisitionFilter);
                    });
                }
            }
            
            if (currentRarityFilter) {
                incompleteCharacters = incompleteCharacters.filter(char => 
                    char.rarity === currentRarityFilter
                );
            }
            
            if (filter) {
                incompleteCharacters = incompleteCharacters.filter(char => {
                    var texts = [char.name, char.acquisition || ''];
                    var charSkins = getSkinsByCharacter(char.id);
                    for (var si = 0; si < charSkins.length; si++) {
                        texts.push(charSkins[si].name);
                    }
                    var score = bestMatchScore(filter, texts);
                    if (score < 0) return false;
                    searchScores.set(char.id, score);
                    return true;
                });
            }

            // 判断角色是否当前无法获取（所有标签仅有"限时活动"/"联动"/"暂无"）
            function isCurrentlyUnavailable(character) {
                const tagStr = character.acquisition || '';
                const tags = tagStr.split('、').map(function(t) { return t.trim(); }).filter(function(t) { return t; });
                if (tags.length === 0) return false; // 完全无标签不视为
                const unavailableTags = ['限时活动', '联动', '暂无'];
                for (var ti = 0; ti < tags.length; ti++) {
                    if (unavailableTags.indexOf(tags[ti]) === -1) {
                        return false;
                    }
                }
                return true;
            }

            if (incompleteCharacters.length === 0) {
                const hasFilter = Boolean(filter || currentAcquisitionFilter || currentRarityFilter);
                const emptyMessage = hasFilter ? '当前筛选下没有匹配角色，请调整搜索词或筛选条件。'
                    : characters.length ? '当前没有待完成的换装记录。' : '还没有记录，先添加一个角色吧。';
                incompleteList.innerHTML = `<div class="flex flex-col items-center justify-center py-12 text-gray-500"><i class="fa ${hasFilter ? 'fa-search' : 'fa-check-circle'} text-4xl mb-3 opacity-50"></i><p>${emptyMessage}</p></div>`;
                return;
            }

            incompleteCharacters.sort((a, b) => {
                // 有搜索词时，按匹配分数降序排列
                if (filter) {
                    var aScore = searchScores.get(a.id) || 0;
                    var bScore = searchScores.get(b.id) || 0;
                    if (aScore !== bScore) {
                        return bScore - aScore;
                    }
                }

                const rarityOrder = {
                    'ultra_rare': 5,
                    'super_rare': 4,
                    'elite': 3,
                    'rare': 2,
                    'common': 1
                };
                return rarityOrder[b.rarity] - rarityOrder[a.rarity];
            });

            // 当开启"隐藏无法获取角色"时，将角色分为普通组 + 额外的无法获取组
            let normalCharacters = incompleteCharacters;
            let unavailableCharacters = [];
            if (hideUnavailable) {
                normalCharacters = [];
                for (var ui = 0; ui < incompleteCharacters.length; ui++) {
                    if (isCurrentlyUnavailable(incompleteCharacters[ui])) {
                        unavailableCharacters.push(incompleteCharacters[ui]);
                    } else {
                        normalCharacters.push(incompleteCharacters[ui]);
                    }
                }
            }

            function renderRarityGroups(characters, customTitle, customColorBarClass, hideIfEmpty) {
                const rarityGroups = {
                    'ultra_rare': { name: '海上传奇', characters: [] },
                    'super_rare': { name: '超稀有', characters: [] },
                    'elite': { name: '精锐', characters: [] },
                    'rare': { name: '稀有', characters: [] },
                    'common': { name: '普通', characters: [] }
                };

                characters.forEach(character => {
                    if (rarityGroups[character.rarity]) {
                        rarityGroups[character.rarity].characters.push(character);
                    }
                });

                const rarityOrderArr = ['ultra_rare', 'super_rare', 'elite', 'rare', 'common'];

                if (customTitle) {
                    // 自定义大分组（整个"无法获取角色"作为一个大分组，内部仍按稀有度显示）
                    if (hideIfEmpty && characters.length === 0) return;

                    const bigGroupContainer = document.createElement('div');
                    bigGroupContainer.className = 'mb-6 border-t border-gray-200 pt-6';

                    const bigGroupTitle = document.createElement('div');
                    bigGroupTitle.className = 'flex items-center mb-4';
                    bigGroupTitle.innerHTML = `
                        <div class="w-2 h-6 ${customColorBarClass || 'bg-gray-400'} rounded-full mr-2"></div>
                        <h3 class="font-bold text-gray-700">${customTitle} <span class="text-sm font-normal text-gray-500">(${characters.length})</span></h3>
                    `;
                    bigGroupContainer.appendChild(bigGroupTitle);

                    const innerGrid = document.createElement('div');
                    innerGrid.className = 'grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5 gap-3';

                    // 内部所有角色按稀有度顺序平铺到一个网格（不单独显示每个稀有度小标题，避免过度分组）
                    rarityOrderArr.forEach(rarityKey => {
                        const group = rarityGroups[rarityKey];
                        group.characters.forEach(character => {
                            renderCharacterCardInto(character, innerGrid);
                        });
                    });

                    bigGroupContainer.appendChild(innerGrid);
                    incompleteList.appendChild(bigGroupContainer);
                    return;
                }

                // 默认：每个稀有度一个独立分组（原有行为）
                rarityOrderArr.forEach(rarityKey => {
                    const group = rarityGroups[rarityKey];
                    if (group.characters.length === 0) return;

                    const groupContainer = document.createElement('div');
                    groupContainer.className = 'mb-6';

                    const groupTitle = document.createElement('div');
                    groupTitle.className = 'flex items-center mb-3';
                    groupTitle.innerHTML = `
                        <div class="w-2 h-6 bg-rarity-${rarityKey} rounded-full mr-2"></div>
                        <h3 class="font-semibold text-gray-800">${group.name} <span class="text-sm font-normal text-gray-500">(${group.characters.length})</span></h3>
                    `;
                    groupContainer.appendChild(groupTitle);

                    const charactersGrid = document.createElement('div');
                    charactersGrid.className = 'grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5 gap-3';

                    group.characters.forEach(character => {
                        renderCharacterCardInto(character, charactersGrid);
                    });

                    groupContainer.appendChild(charactersGrid);
                    incompleteList.appendChild(groupContainer);
                });
            }

            function renderCharacterCardInto(character, container) {
                const characterSkins = getSkinsByCharacter(character.id);
                const totalCount = characterSkins.length;
                const collectedCount = getEffectiveCollected(character);
                const targetCount = getTargetCount(character);
                const isSharedCompleted = isCharacterCompletedViaShareable(character);
                const nameColorClass = isSharedCompleted ? 'text-yellow-600' : '';

                const itemElement = document.createElement('div');
                itemElement.setAttribute('onclick', `showCharacterDetail('${character.id}', true)`);
                itemElement.dataset.detailCharacterId = character.id;
                itemElement.tabIndex = 0;
                itemElement.setAttribute('aria-label', `${character.name}，查看角色详情`);
                itemElement.setAttribute('aria-haspopup', 'dialog');
                itemElement.addEventListener('keydown', event => {
                    if (event.target === itemElement && (event.key === 'Enter' || event.key === ' ')) {
                        event.preventDefault();
                        showCharacterDetail(character.id, true);
                    }
                });
                itemElement.className = `p-3 flex flex-col border-2 border-rarity-${character.rarity} rounded-xl cursor-pointer hover:shadow-md hover:-translate-y-0.5 transition-all duration-200`;

                itemElement.innerHTML = `
                    <div class="flex flex-col flex-1">
                        <div class="flex items-center justify-between">
                            <span class="font-medium truncate min-w-0 flex-1 mr-2 ${nameColorClass}">${escapeHtml(character.name)}${isSharedCompleted ? ' <i class="fa fa-star-half-o text-amber-500 text-xs ml-0.5" title="仅靠可共享换装达到目标"></i>' : ''}</span>
                            <div class="flex items-center flex-shrink-0">
                                <div class="flex items-center border rounded-lg overflow-hidden shadow-sm">
                                    <button type="button" class="px-2 py-1 bg-gray-100 hover:bg-gray-200 transition-colors text-sm" data-character-id="${character.id}" data-change="-1" data-max="${targetCount}">-</button>
                                    <input type="text" value="${collectedCount}"
                                        class="w-8 text-center border-x-0 focus:outline-none flex items-center justify-center h-8 font-medium"
                                        data-character-id="${character.id}" data-max="${targetCount}"
                                        onkeypress="if(event.key === 'Enter') this.blur()"
                                        style="text-align: center; padding: 0;">
                                    <button type="button" class="px-2 py-1 bg-gray-100 hover:bg-gray-200 transition-colors text-sm" data-character-id="${character.id}" data-change="1" data-max="${targetCount}">+</button>
                                </div>
                                <span class="mx-1 text-gray-500 text-sm">/</span>
                                <span class="font-semibold text-primary text-sm min-w-[28px]">${targetCount}</span>
                            </div>
                        </div>
                        <div class="flex flex-wrap gap-1.5 mt-1.5" id="incomplete-tags-${character.id}"></div>
                    </div>
                `;
                container.appendChild(itemElement);

                // 渲染标签
                const tagsContainer = itemElement.querySelector(`#incomplete-tags-${character.id}`);
                const acquisition = character.acquisition || '';
                const tags = acquisition.split('、').map(tag => tag.trim()).filter(tag => tag);
                if (tags.length === 0) {
                    tags.push('未设置');
                }
                tags.forEach(tag => {
                    const tagElement = document.createElement('span');
                    const colorClass = getTagColorClass(tag);
                    tagElement.className = `text-xs px-2 py-0.5 rounded border ${colorClass} cursor-pointer transition-colors hover:brightness-95`;
                    tagElement.textContent = tag;
                    tagElement.onclick = function(e) {
                        e.stopPropagation();
                        currentAcquisitionFilter = currentAcquisitionFilter === tag ? '' : tag;
                        document.querySelectorAll('.acq-filter-btn').forEach(btn => {
                            btn.classList.remove('bg-blue-100', 'text-blue-700', 'font-medium');
                            btn.classList.add('hover:bg-gray-100');
                        });
                        const allBtn = document.getElementById('acq-filter-all');
                        if (allBtn) {
                            if (!currentAcquisitionFilter) {
                                allBtn.classList.remove('hover:bg-gray-100');
                                allBtn.classList.add('bg-blue-100', 'text-blue-700', 'font-medium');
                            } else {
                                const matched = document.querySelectorAll(`.acq-filter-btn[data-type="${tag.replace(/"/g, '\\"')}"]`);
                                matched.forEach(m => {
                                    m.classList.remove('hover:bg-gray-100');
                                    m.classList.add('bg-blue-100', 'text-blue-700', 'font-medium');
                                });
                            }
                        }
                        filterIncompleteCharacters();
                    };
                    tagsContainer.appendChild(tagElement);
                });
            }

            // 先渲染"普通可获取"角色（使用原有 5 个稀有度分组）
            renderRarityGroups(normalCharacters, null, null, false);

            // 如果开启了隐藏模式且有无法获取角色，最底部再渲染一个"无法获取角色"大分组
            if (hideUnavailable && unavailableCharacters.length > 0) {
                renderRarityGroups(unavailableCharacters, '无法获取角色', 'bg-gray-400', true);
            }

            // 统一绑定增量按钮和输入框事件（针对 incompleteList 所有新生成元素）
            (function bindIncompleteControls() {
                const allButtons = incompleteList.querySelectorAll('button[data-character-id]');
                allButtons.forEach(button => {
                    const newButton = button.cloneNode(true);
                    button.parentNode.replaceChild(newButton, button);
                    newButton.addEventListener('click', function(e) {
                        e.stopPropagation();
                        const characterId = this.dataset.characterId;
                        const change = parseInt(this.dataset.change, 10);
                        const max = parseInt(this.dataset.max, 10);
                        changeCollectedCount(characterId, change, max);
                    });
                });

                const allInputs = incompleteList.querySelectorAll('input[data-character-id]');
                allInputs.forEach(input => {
                    input.addEventListener('change', function(e) {
                        e.stopPropagation();
                        const characterId = this.dataset.characterId;
                        const value = this.value;
                        const max = parseInt(this.dataset.max, 10);
                        updateCollectedCountFromInput(characterId, value, max);
                    });
                    input.addEventListener('click', function(e) { e.stopPropagation(); });
                    input.addEventListener('keypress', function(e) {
                        if (e.key === 'Enter') this.blur();
                    });
                });
            })();
        }
        function renderAcquisitionChart() {
            const incompleteCharacters = characters.filter(character => {
                const characterSkins = getSkinsByCharacter(character.id);
                const ownedCount = characterSkins.filter(skin => skin.owned).length;
                const totalCount = characterSkins.length;
                const targetCount = getTargetCount(character);
                return totalCount >= 0 && (ownedCount < totalCount || !isCharacterCompleted(character));
            });
            
            const acquisitionStats = {};
            let noAcquisitionCount = 0;
            const noOwnedSkinsCount = characters.filter(hasNoOwnedSkins).length;
            let unusedSkinsCount = 0;
            
            incompleteCharacters.forEach(character => {
                const acquisition = character.acquisition || '';
                const trimmedAcquisition = acquisition.trim();
                
                const characterSkins = getSkinsByCharacter(character.id);
                const ownedSkinsCount = characterSkins.filter(skin => skin.owned).length;
                const collectedCount = getEffectiveCollected(character);
                if (ownedSkinsCount > collectedCount) {
                    unusedSkinsCount++;
                }
                
                if (!trimmedAcquisition) {
                    noAcquisitionCount++;
                } else {
                    const acquisitionMethods = trimmedAcquisition.split('、').map(method => method.trim()).filter(method => method);
                    acquisitionMethods.forEach(method => {
                        if (method !== '未设置') {
                            acquisitionStats[method] = (acquisitionStats[method] || 0) + 1;
                        }
                    });
                }
            });
            
            const labels = Object.keys(acquisitionStats);
            if (noAcquisitionCount > 0) {
                labels.push('未拥有获取方式');
            }
            if (noOwnedSkinsCount > 0) {
                labels.push('未拥有换装角色');
            }
            if (unusedSkinsCount > 0) {
                labels.push('拥有未使用的换装');
            }
            renderAcquisitionFilter(labels, noAcquisitionCount, noOwnedSkinsCount, unusedSkinsCount);
        }
        
        function getSortOrder() {
            return readStoredArray('azurLaneAcquisitionSortOrder').filter(item => typeof item === 'string');
        }
        
        function updateSortOrder() {
            const filterContainer = document.getElementById('acquisition-filter');
            const buttons = filterContainer.querySelectorAll('button:not(:first-child)');
            const sortOrder = [];
            
            buttons.forEach(button => {
                const type = button.dataset.type;
                if (type) {
                    sortOrder.push(type);
                }
            });
            
            localStorage.setItem('azurLaneAcquisitionSortOrder', JSON.stringify(sortOrder));
        }
        
        function applySortOrder(acquisitionTypes) {
            const sortOrder = getSortOrder();
            if (sortOrder.length === 0) return acquisitionTypes;
            
            return [...acquisitionTypes].sort((a, b) => {
                const aIndex = sortOrder.indexOf(a);
                const bIndex = sortOrder.indexOf(b);
                
                if (aIndex !== -1 && bIndex !== -1) {
                    return aIndex - bIndex;
                } else if (aIndex !== -1) {
                    return -1;
                } else if (bIndex !== -1) {
                    return 1;
                } else {
                    return a.localeCompare(b);
                }
            });
        }
        
        function renderAcquisitionFilter(acquisitionTypes, noAcquisitionCount = 0, noOwnedSkinsCount = 0, unusedSkinsCount = 0) {
            const filterContainer = document.getElementById('acquisition-filter');
            filterContainer.innerHTML = '';
            
            const sortedTypes = applySortOrder(acquisitionTypes);
            
            const allButton = document.createElement('button');
            let totalCount = 0;
            characters.forEach(char => {
                const characterSkins = getSkinsByCharacter(char.id);
                const totalSkins = characterSkins.length;
                const rawCollected = parseInt(char.collected, 10) || 0;
                const targetCount = getTargetCount(char);
                if (totalSkins === 0 || rawCollected < targetCount) {
                    totalCount += Math.max(0, targetCount - rawCollected);
                }
            });
            allButton.className = `w-full text-left px-3 py-2.5 rounded-lg text-sm ${!currentAcquisitionFilter ? 'bg-blue-100 text-blue-700 font-medium' : 'hover:bg-gray-100'} transition-colors duration-200`;
            allButton.innerHTML = `全部 <span class="float-right text-gray-500">${totalCount}</span>`;
            allButton.addEventListener('click', () => {
                currentAcquisitionFilter = '';
                currentCharacterAcquisitionFilter = '';
                const currentSearchFilter = searchInput.value.trim();
                const currentIncompleteFilter = document.getElementById('incomplete-search').value.trim();
                renderIncompleteList(currentIncompleteFilter);
                renderCharacterList(currentSearchFilter);
                renderAcquisitionFilter(sortedTypes, noAcquisitionCount, noOwnedSkinsCount, unusedSkinsCount);
            });
            filterContainer.appendChild(allButton);
            
            const dropZoneBottom = document.createElement('div');
            dropZoneBottom.className = 'h-8 rounded-lg my-1 transition-all duration-200';
            dropZoneBottom.id = 'drop-zone-bottom';
            
            dropZoneBottom.addEventListener('dragover', function(e) {
                e.preventDefault();
                e.dataTransfer.dropEffect = 'move';
                this.classList.add('bg-blue-50', 'border-t-2', 'border-blue-400');
            });
            
            dropZoneBottom.addEventListener('dragleave', function() {
                this.classList.remove('bg-blue-50', 'border-t-2', 'border-blue-400');
            });
            
            dropZoneBottom.addEventListener('drop', function(e) {
                e.preventDefault();
                
                const draggedButton = filterContainer.querySelector('.opacity-50');
                if (draggedButton) {
                    filterContainer.removeChild(draggedButton);
                    filterContainer.insertBefore(draggedButton, this);
                    
                    updateSortOrder();
                    renderAcquisitionChart();
                }
                
                this.classList.remove('bg-blue-50', 'border-t-2', 'border-blue-400');
            });
            
            sortedTypes.forEach(type => {
                if (type === '未设置') return;
                
                let count = 0;
                if (type === '未拥有获取方式') {
                    characters.forEach(char => {
                        const acquisition = char.acquisition || '';
                        if (acquisition.trim()) {
                            return;
                        }
                        
                        const characterSkins = getSkinsByCharacter(char.id);
                        const totalCount = characterSkins.length;
                        const rawCollected = parseInt(char.collected, 10) || 0;
                        const targetCount = getTargetCount(char);
                        if (totalCount === 0 || rawCollected < targetCount) {
                            count += Math.max(0, targetCount - rawCollected);
                        }
                    });
                } else if (type === '未拥有换装角色') {
                    count = noOwnedSkinsCount;
                } else if (type === '拥有未使用的换装') {
                    characters.forEach(char => {
                        const characterSkins = getSkinsByCharacter(char.id);
                        const ownedSkinsCount = characterSkins.filter(skin => skin.owned).length;
                        const effectiveCollected = getEffectiveCollected(char);
                        if (ownedSkinsCount > effectiveCollected) {
                            const rawCollected = parseInt(char.collected, 10) || 0;
                            const targetCount = getTargetCount(char);
                            if (rawCollected < targetCount) {
                                count += Math.max(0, targetCount - rawCollected);
                            }
                        }
                    });
                } else {
                    characters.forEach(char => {
                        const acquisition = char.acquisition || '未设置';
                        const acquisitionMethods = acquisition.split('、').map(method => method.trim()).filter(method => method);
                        if (!acquisitionMethods.includes(type)) {
                            return;
                        }
                        
                        const characterSkins = getSkinsByCharacter(char.id);
                        const totalCount = characterSkins.length;
                        const rawCollected = parseInt(char.collected, 10) || 0;
                        const targetCount = getTargetCount(char);
                        if (totalCount === 0 || rawCollected < targetCount) {
                            count += Math.max(0, targetCount - rawCollected);
                        }
                    });
                }
                
                const button = document.createElement('button');
                button.className = `w-full text-left px-3 py-2.5 rounded-lg text-sm cursor-move ${currentAcquisitionFilter === type ? 'bg-blue-100 text-blue-700 font-medium' : 'hover:bg-gray-100'} transition-all duration-200`;
                button.innerHTML = `${escapeHtml(type)} <span class="float-right text-gray-500">${count}${type === '未拥有换装角色' ? ' 位' : ''}</span>`;
                if (type === '未拥有换装角色') button.title = '未拥有任何换装的角色数量（含未录入换装），与角色收集进度无关';
                button.draggable = true;
                button.dataset.type = type;
                
                button.addEventListener('click', () => {
                    currentAcquisitionFilter = type;
                    currentCharacterAcquisitionFilter = type;
                    const currentSearchFilter = searchInput.value.trim();
                    const currentIncompleteFilter = document.getElementById('incomplete-search').value.trim();
                    renderIncompleteList(currentIncompleteFilter);
                    renderCharacterList(currentSearchFilter);
                    renderAcquisitionFilter(sortedTypes, noAcquisitionCount, noOwnedSkinsCount, unusedSkinsCount);
                });
                
                button.addEventListener('dragstart', function(e) {
                    this.classList.add('opacity-50', 'border-2', 'border-blue-500', 'scale-105');
                    e.dataTransfer.effectAllowed = 'move';
                });
                
                button.addEventListener('dragend', function() {
                    this.classList.remove('opacity-50', 'border-2', 'border-blue-500', 'scale-105');
                    document.querySelectorAll('#acquisition-filter button').forEach(btn => {
                        btn.classList.remove('bg-blue-50', 'border-t-2', 'border-blue-400', 'border-b-2');
                    });
                });
                
                button.addEventListener('dragover', function(e) {
                    e.preventDefault();
                    e.dataTransfer.dropEffect = 'move';
                });
                
                button.addEventListener('dragenter', function(e) {
                    e.preventDefault();
                    if (!this.classList.contains('opacity-50')) {
                        document.querySelectorAll('#acquisition-filter button').forEach(btn => {
                            btn.classList.remove('border-t-2', 'border-b-2', 'border-blue-400');
                        });
                        this.classList.add('border-t-2', 'border-blue-400');
                    }
                });
                
                button.addEventListener('dragleave', function() {
                    this.classList.remove('border-t-2', 'border-blue-400');
                });
                
                button.addEventListener('drop', function(e) {
                    e.preventDefault();
                    
                    const filterContainer = document.getElementById('acquisition-filter');
                    const draggedButton = filterContainer.querySelector('.opacity-50');
                    
                    if (draggedButton && draggedButton !== this) {
                        e.stopPropagation();
                        
                        const draggedIndex = Array.from(filterContainer.children).indexOf(draggedButton);
                        const targetIndex = Array.from(filterContainer.children).indexOf(this);
                        
                        filterContainer.removeChild(draggedButton);
                        
                        if (draggedIndex < targetIndex) {
                            filterContainer.insertBefore(draggedButton, filterContainer.children[targetIndex - 1]);
                        } else {
                            filterContainer.insertBefore(draggedButton, filterContainer.children[targetIndex]);
                        }
                        
                        updateSortOrder();
                        renderAcquisitionChart();
                    }
                    
                    document.querySelectorAll('#acquisition-filter button').forEach(btn => {
                        btn.classList.remove('border-t-2', 'border-b-2', 'border-blue-400');
                    });
                });
                
                filterContainer.appendChild(button);
            });
            
            filterContainer.appendChild(dropZoneBottom);
        }
        
        function changeCollectedCount(characterId, change, maxValue) {
            const character = characters.find(char => char.id === characterId);
            if (character) {
                const currentValue = parseInt(character.collected, 10) || 0;
                // 允许收集数量超过目标总数量，仅限制下限为0
                const newValue = Math.max(0, currentValue + change);
                
                if (newValue !== currentValue) {
                    updateCollectedCount(characterId, newValue.toString());
                }
            }
        }
        
        function updateCollectedCount(characterId, value) {
            const character = characters.find(char => char.id === characterId);
            if (character) {
                const nextCount = parseInt(value, 10);
                const currentCount = parseInt(character.collected, 10) || 0;
                // This setter receives the independent count; the input adapter has already subtracted shared counts.
                if (!Number.isSafeInteger(nextCount) || nextCount < 0 || nextCount === currentCount) return;
                saveUndoState();
                
                const oldEffective = getEffectiveCollected(character);
                character.collected = String(nextCount);
                character.updatedAt = new Date().toISOString();
                const newEffective = getEffectiveCollected(character);
                const targetCount = getTargetCount(character);
                
                addActivityLog({
                    type: 'update',
                    target: 'character',
                    id: character.id,
                    name: character.name,
                    logMessage: `更新收集进度从 ${oldEffective}/${targetCount} 到 ${newEffective}/${targetCount}`,
                    timestamp: character.updatedAt
                });
                
                saveData();
                scheduleRender();
            }
        }
        
        function updateCollectedCountFromInput(characterId, inputValue, totalCount) {
            const character = characters.find(char => char.id === characterId);
            if (!character) return;
            
            let newCount = parseInt(inputValue, 10);
            if (isNaN(newCount)) {
                const inputs = document.querySelectorAll(`input[data-character-id="${characterId}"]:not([data-field])`);
                inputs.forEach(input => {
                    input.value = getEffectiveCollected(character);
                });
                return;
            }
            
            // 输入框显示的是现有收集数量（= 原逻辑数量 + 可共享换装数量），
            // 存储时需减去可共享换装数量，得到原逻辑数量
            const shareableCount = parseInt(character.shareableSkinCount, 10) || 0;
            // 允许收集数量超过目标总数量，仅限制下限为0
            newCount = Math.max(0, newCount - shareableCount);
            updateCollectedCount(characterId, newCount.toString());
            // A displayed total cannot be smaller than its shared component.
            // Restore the canonical display even when the independent value did not change.
            document.querySelectorAll(`input[data-character-id="${characterId}"]:not([data-field])`).forEach(input => {
                input.value = getEffectiveCollected(character);
            });
        }
        
        function openCharacterModal(characterId = null) {
            const modalTitle = document.getElementById('modal-title');
            const characterName = document.getElementById('character-name');
            const characterRarity = document.getElementById('character-rarity');
            const characterFormId = document.getElementById('character-id');
            const acquisitionField = document.getElementById('acquisition-field');
            const characterAcquisition = document.getElementById('character-acquisition');
            const customTargetField = document.getElementById('custom-target-field');
            const customTargetInput = document.getElementById('character-custom-target');
            const customTargetError = document.getElementById('custom-target-error');
            const extraCountField = document.getElementById('extra-count-field');
            const shareableSkinCountInput = document.getElementById('character-shareable-skin-count');
            
            if (customTargetError) customTargetError.classList.add('hidden');
            
            if (characterId) {
                const character = characters.find(char => char.id === characterId);
                if (!character) return;
                
                modalTitle.textContent = '编辑角色';
                characterName.value = character.name;
                characterRarity.value = character.rarity;
                characterAcquisition.value = character.acquisition;
                characterFormId.value = character.id;
                
                if (customTargetInput) {
                    customTargetInput.value = (character.customTargetCount !== undefined && character.customTargetCount !== null && character.customTargetCount !== '') ? character.customTargetCount : '';
                }
                
                if (shareableSkinCountInput) {
                    shareableSkinCountInput.value = parseInt(character.shareableSkinCount, 10) || 0;
                }
                
                acquisitionField.classList.remove('hidden');
                if (customTargetField) customTargetField.classList.remove('hidden');
                if (extraCountField) extraCountField.classList.remove('hidden');
            } else {
                modalTitle.textContent = '添加角色';
                characterForm.reset();
                characterFormId.value = '';
                
                if (customTargetInput) customTargetInput.value = '';
                if (shareableSkinCountInput) shareableSkinCountInput.value = '0';
                
                acquisitionField.classList.remove('hidden');
                if (customTargetField) customTargetField.classList.add('hidden');
                if (extraCountField) extraCountField.classList.remove('hidden');
            }
            
            characterModal.classList.remove('hidden');
            characterName.focus();
        }
        
        function saveCharacter(e) {
            try {
                e.preventDefault();
                
                const characterIdInput = document.getElementById('character-id');
                const nameInput = document.getElementById('character-name');
                const rarityInput = document.getElementById('character-rarity');
                const acquisitionInput = document.getElementById('character-acquisition');
                const customTargetInput = document.getElementById('character-custom-target');
                const customTargetError = document.getElementById('custom-target-error');
                const shareableSkinCountInput = document.getElementById('character-shareable-skin-count');
                
                if (!characterIdInput || !nameInput || !rarityInput) {
                    alert('错误：找不到表单元素');
                    return;
                }
                
                const characterId = characterIdInput.value;
                const name = nameInput.value.trim();
                const rarity = rarityInput.value;
                const timestamp = new Date().toISOString();
                
                if (!name) {
                    alert('请输入角色名称');
                    nameInput.focus();
                    return;
                }
                
                const acquisition = acquisitionInput ? acquisitionInput.value.trim() : '';
                
                // 可共享换装角色数量验证与提取
                let shareableSkinCount = '0';
                if (shareableSkinCountInput) {
                    const rawShareable = parseInt(shareableSkinCountInput.value, 10);
                    shareableSkinCount = (!isNaN(rawShareable) && rawShareable >= 0) ? rawShareable.toString() : '0';
                }
                
                // 自定义目标数量验证
                let customTargetCount = null;
                let customTargetChanged = false;
                let oldCustomTargetCount = null;
                
                if (characterId && customTargetInput && customTargetInput.value.trim() !== '') {
                    const rawValue = customTargetInput.value.trim();
                    const numValue = Number(rawValue);
                    
                    if (!Number.isInteger(numValue)) {
                        if (customTargetError) {
                            customTargetError.textContent = '请输入整数';
                            customTargetError.classList.remove('hidden');
                        }
                        customTargetInput.focus();
                        return;
                    }
                    
                    if (numValue < 0) {
                        if (customTargetError) {
                            customTargetError.textContent = '目标数量不能小于 0';
                            customTargetError.classList.remove('hidden');
                        }
                        customTargetInput.focus();
                        return;
                    }
                    
                    if (numValue > 999) {
                        if (customTargetError) {
                            customTargetError.textContent = '目标数量不能超过 999';
                            customTargetError.classList.remove('hidden');
                        }
                        customTargetInput.focus();
                        return;
                    }
                    
                    const targetCharacter = characters.find(char => char.id === characterId);
                    if (targetCharacter) {
                        // 允许收集数量超过目标数量，因此不再限制目标数量必须大于等于已收集数量
                        oldCustomTargetCount = (targetCharacter.customTargetCount !== undefined && targetCharacter.customTargetCount !== null && targetCharacter.customTargetCount !== '') ? targetCharacter.customTargetCount : null;
                    }
                    
                    customTargetCount = numValue;
                } else if (characterId) {
                    const targetCharacter = characters.find(char => char.id === characterId);
                    if (targetCharacter) {
                        oldCustomTargetCount = (targetCharacter.customTargetCount !== undefined && targetCharacter.customTargetCount !== null && targetCharacter.customTargetCount !== '') ? targetCharacter.customTargetCount : null;
                    }
                    customTargetCount = null;
                }
                
                if (customTargetError) customTargetError.classList.add('hidden');
                
                if (characterId && customTargetCount !== oldCustomTargetCount) {
                    customTargetChanged = true;
                }
                
                saveUndoState();
            
                if (characterId) {
                    const index = characters.findIndex(char => char.id === characterId);
                    if (index !== -1) {
                        const oldName = characters[index].name;
                        
                        characters[index] = { 
                            id: characterId, 
                            name, 
                            rarity, 
                            acquisition, 
                            collected: characters[index].collected, 
                            customTargetCount: customTargetCount,
                            shareableSkinCount: shareableSkinCount,
                            createdAt: characters[index].createdAt || timestamp,
                            updatedAt: timestamp 
                        };
                        
                        let logMessage = '';
                        if (oldName !== name) {
                            logMessage = `将角色"${oldName}"重命名为"${name}"`;
                        } else if (customTargetChanged) {
                            const oldDisplay = oldCustomTargetCount !== null ? oldCustomTargetCount : '默认';
                            const newDisplay = customTargetCount !== null ? customTargetCount : '默认';
                            logMessage = `更新角色目标收集数量：${oldDisplay} → ${newDisplay}`;
                        } else {
                            logMessage = `更新了角色"${name}"的信息`;
                        }
                        
                        addActivityLog({
                            type: 'update',
                            target: 'character',
                            id: characterId,
                            name: name,
                            oldName: oldName,
                            logMessage: logMessage,
                            timestamp: timestamp
                        });
                        
                        saveData();
                        characterModal.classList.add('hidden');
                        scheduleRender();
                    } else {
                        alert('错误：未找到要更新的角色');
                    }
                } else {
                    const existingCharacter = characters.find(char => char.name === name);
                    if (existingCharacter) {
                        if (confirm(`已存在名为"${name}"的角色，是否覆盖？`)) {
                            const index = characters.findIndex(char => char.id === existingCharacter.id);
                            characters[index] = { 
                                id: existingCharacter.id, 
                                name, 
                                rarity, 
                                acquisition, 
                                collected: existingCharacter.collected,
                                shareableSkinCount: shareableSkinCount,
                                createdAt: existingCharacter.createdAt,
                                updatedAt: timestamp 
                            };
                            
                            addActivityLog({
                                type: 'update',
                                target: 'character',
                                id: existingCharacter.id,
                                name: name,
                                oldName: existingCharacter.name,
                                timestamp: timestamp
                            });
                            
                            saveData();
                            characterModal.classList.add('hidden');
                            scheduleRender();
                            return;
                        } else {
                            return;
                        }
                    }
                    
                    const newCharacter = {
                        id: Date.now().toString(),
                        name,
                        rarity,
                        acquisition,
                        collected: '0',
                        shareableSkinCount: shareableSkinCount,
                        createdAt: timestamp,
                        updatedAt: timestamp
                    };
                    characters.push(newCharacter);
                    
                    addActivityLog({
                        type: 'add',
                        target: 'character',
                        id: newCharacter.id,
                        name: name,
                        timestamp: timestamp
                    });
                    
                    saveData();
                    characterModal.classList.add('hidden');
                    currentCharacterId = newCharacter.id;
                    scheduleRender();
                }
                } catch (error) {
                    console.error('保存角色时发生错误:', error);
                    alert('保存失败：' + error.message);
                }
        }
        
        function openSkinModal(skinId = null) {
            const modalTitle = document.getElementById('skin-modal-title');
            const skinName = document.getElementById('skin-name');
            const skinAcquisition = document.getElementById('skin-acquisition');
            const skinOwned = document.getElementById('skin-owned');
            const skinFormId = document.getElementById('skin-id');
            const skinCharacterId = document.getElementById('skin-character-id');
            
            if (skinId) {
                const skin = skins.find(s => s.id === skinId);
                if (!skin) return;
                
                modalTitle.textContent = '编辑换装';
                skinName.value = skin.name;
                skinAcquisition.value = skin.acquisition;
                skinOwned.checked = skin.owned;
                skinFormId.value = skin.id;
                skinCharacterId.value = skin.characterId;
            } else {
                if (!currentCharacterId) {
                    alert('请先选择一个角色');
                    return;
                }
                
                modalTitle.textContent = '添加换装';
                skinForm.reset();
                skinFormId.value = '';
                skinCharacterId.value = currentCharacterId;
            }
            
            skinModal.classList.remove('hidden');
            skinName.focus();
        }
        
        function saveSkin(e) {
            e.preventDefault();
            
            const skinId = document.getElementById('skin-id').value;
            const characterId = document.getElementById('skin-character-id').value;
            const name = document.getElementById('skin-name').value;
            const acquisition = document.getElementById('skin-acquisition').value;
            const owned = document.getElementById('skin-owned').checked;
            const timestamp = new Date().toISOString();
            
            saveUndoState();
            
            const character = characters.find(char => char.id === characterId);
            const characterName = character ? character.name : '未知角色';
            
            if (skinId) {
                const index = skins.findIndex(s => s.id === skinId);
                if (index !== -1) {
                    const oldName = skins[index].name;
                    const oldOwned = skins[index].owned;
                    skins[index] = { ...skins[index], id: skinId, characterId, name, acquisition, owned, updatedAt: timestamp };
                    markSkinsIndexDirty();
                    
                    let logMessage = '';
                    if (oldName !== name) {
                        logMessage = `将换装"${oldName}"重命名为"${name}"`;
                    } else if (oldOwned !== owned) {
                        logMessage = owned ? `标记换装"${name}"为已拥有` : `取消标记换装"${name}"为已拥有`;
                    } else {
                        logMessage = `更新了换装"${name}"的信息`;
                    }
                    
                    addActivityLog({
                        type: 'update',
                        target: 'skin',
                        id: skinId,
                        name: name,
                        characterId: characterId,
                        characterName: characterName,
                        logMessage: logMessage,
                        timestamp: timestamp
                    });
                }
            } else {
                const newSkin = {
                    id: Date.now().toString() + Math.random().toString(36).slice(2, 11),
                    characterId,
                    name,
                    acquisition,
                    owned,
                    createdAt: timestamp,
                    updatedAt: timestamp
                };
                skins.push(newSkin);
                markSkinsIndexDirty();

                const character = characters.find(char => char.id === characterId);
                if (character) {
                    character.updatedAt = timestamp;
                    
                    // 如果角色设置了自定义目标收集数量，添加换装时自动+1
                    if (character.customTargetCount !== undefined && character.customTargetCount !== null && character.customTargetCount !== '') {
                        const oldTarget = Number(character.customTargetCount);
                        const newTarget = oldTarget + 1;
                        character.customTargetCount = newTarget;
                        
                        addActivityLog({
                            type: 'update',
                            target: 'character',
                            id: character.id,
                            name: character.name,
                            logMessage: `新增换装，自动调整目标收集数量：${oldTarget} → ${newTarget}`,
                            timestamp: timestamp
                        });
                    }
                }
                
                addActivityLog({
                    type: 'add',
                    target: 'skin',
                    id: newSkin.id,
                    name: name,
                    characterId: characterId,
                    characterName: characterName,
                    timestamp: timestamp
                });
            }
            
            saveData();
            skinModal.classList.add('hidden');
            scheduleRender();
            updateUncollectedValueDisplay();
        }
        
        function toggleSkinOwned(skinId) {
            const skin = skins.find(s => s.id === skinId);
            if (skin) {
                saveUndoState();
                const oldOwned = skin.owned;
                skin.owned = !skin.owned;
                skin.updatedAt = new Date().toISOString();
                
                const character = characters.find(char => char.id === skin.characterId);
                const characterName = character ? character.name : '未知角色';
                
                addActivityLog({
                    type: 'update',
                    target: 'skin',
                    id: skin.id,
                    name: skin.name,
                    characterId: skin.characterId,
                    characterName: characterName,
                    logMessage: skin.owned ? `标记换装"${skin.name}"为已拥有` : `取消标记换装"${skin.name}"为已拥有`,
                    timestamp: skin.updatedAt
                });
                
                saveData();
                scheduleRender();
                updateUncollectedValueDisplay();
            }
        }
        
        function editSkinAcquisition(skinId, element) {
            const skin = skins.find(s => s.id === skinId);
            if (!skin) return;
            
            const currentAcquisition = skin.acquisition || '';
            const input = document.createElement('input');
            input.type = 'text';
            input.value = currentAcquisition;
            input.className = 'text-xs px-2 py-0.5 border border-blue-300 rounded focus:outline-none focus:ring-1 focus:ring-blue-500 bg-white';
            input.style.width = 'auto';
            input.style.minWidth = '100px';
            input.addEventListener('click', event => event.stopPropagation());
            
            element.replaceWith(input);
            input.focus();
            input.select();
            
            function handleClickOutside(e) {
                if (!input.contains(e.target)) {
                    document.removeEventListener('click', handleClickOutside);
                    saveSkinAcquisition(skinId, input.value, input);
                }
            }
            
            setTimeout(() => {
                document.addEventListener('click', handleClickOutside);
            }, 0);
            
            input.addEventListener('blur', function() {
                document.removeEventListener('click', handleClickOutside);
                saveSkinAcquisition(skinId, this.value, this);
            });
            
            input.addEventListener('keydown', function(e) {
                if (e.key === 'Enter') {
                    document.removeEventListener('click', handleClickOutside);
                    this.blur();
                } else if (e.key === 'Escape') {
                    document.removeEventListener('click', handleClickOutside);
                    const newSpan = document.createElement('span');
                    newSpan.className = 'text-sm text-gray-500 cursor-pointer hover:text-blue-600 hover:bg-blue-50 px-1 py-0.5 rounded inline-block min-w-[120px]';
                    newSpan.textContent = currentAcquisition || '点击编辑获取方式';
                    newSpan.onclick = function(evt) {
                        evt.stopPropagation();
                        editSkinAcquisition(skinId, this);
                    };
                    input.replaceWith(newSpan);
                }
            });
        }
        
        function saveSkinAcquisition(skinId, value, originalElement) {
            const skin = skins.find(s => s.id === skinId);
            if (!skin) return;
            
            const oldValue = skin.acquisition;
            const newValue = value.trim();
            
            if (newValue !== oldValue) {
                saveUndoState();
                
                skin.acquisition = newValue;
                skin.updatedAt = new Date().toISOString();
                
                const character = characters.find(char => char.id === skin.characterId);
                const characterName = character ? character.name : '未知角色';
                
                addActivityLog({
                    type: 'update',
                    target: 'skin',
                    id: skinId,
                    name: skin.name,
                    characterId: skin.characterId,
                    characterName: characterName,
                    logMessage: `更新换装获取方式从 "${oldValue || '未设置'}" 到 "${newValue || '未设置'}"`,
                    timestamp: skin.updatedAt
                });
                
                saveData();
                renderAcquisitionChart();
                updateUncollectedValueDisplay();
            }
            
            const newSpan = document.createElement('span');
            newSpan.className = 'text-sm text-gray-500 cursor-pointer hover:text-blue-600 hover:bg-blue-50 px-1 py-0.5 rounded inline-block min-w-[120px]';
            newSpan.textContent = newValue || '点击编辑获取方式';
            newSpan.onclick = function(e) {
                e.stopPropagation();
                editSkinAcquisition(skinId, this);
            };
            
            originalElement.replaceWith(newSpan);
        }
        
        function deleteSkin(skinId) {
            const skin = skins.find(s => s.id === skinId);
            if (skin) {
                if (confirm(`确定要删除换装"${skin.name}"吗？`)) {
                    saveUndoState();
                    
                    const skinIndex = skins.findIndex(s => s.id === skinId);
                    if (skinIndex !== -1) {
                        const characterId = skins[skinIndex].characterId;
                        const skinName = skins[skinIndex].name;
                        const timestamp = new Date().toISOString();
                        
                        const character = characters.find(char => char.id === characterId);
                        const characterName = character ? character.name : '未知角色';
                        
                        skins.splice(skinIndex, 1);
                        markSkinsIndexDirty();
                        
                        // 如果角色设置了自定义目标收集数量，删除换装时自动-1（允许低于已收集数量）
                        if (character && character.customTargetCount !== undefined && character.customTargetCount !== null && character.customTargetCount !== '') {
                            const oldTarget = Number(character.customTargetCount);
                            const newTarget = Math.max(0, oldTarget - 1);
                            if (newTarget !== oldTarget) {
                                character.customTargetCount = newTarget;
                                character.updatedAt = timestamp;
                                
                                addActivityLog({
                                    type: 'update',
                                    target: 'character',
                                    id: character.id,
                                    name: characterName,
                                    logMessage: `删除换装"${skinName}"，自动调整目标收集数量：${oldTarget} → ${newTarget}`,
                                    timestamp: timestamp
                                });
                            } else {
                                character.updatedAt = timestamp;
                            }
                        } else if (character) {
                            character.updatedAt = timestamp;
                        }
                        
                        addActivityLog({
                            type: 'delete',
                            target: 'skin',
                            id: skinId,
                            name: skinName,
                            characterId: characterId,
                            characterName: characterName,
                            timestamp: timestamp
                        });
                        
                        saveData();
                        scheduleRender();
                    }
                }
            }
        }
        
        function filterCharacters() {
            const filter = searchInput.value.trim();
            renderCharacterList(filter);
        }
        
        function filterIncompleteCharacters() {
            const filter = document.getElementById('incomplete-search').value.trim();
            renderIncompleteList(filter);
        }
        
        function exportData() {
            const data = {
                schemaVersion: 1,
                characters,
                skins,
                activityLog,
                acquisitionSortOrder: getSortOrder(),
                exportDate: new Date().toISOString()
            };
            
            const dataStr = JSON.stringify(data, null, 2);
            const dataBlob = new Blob([dataStr], { type: 'application/json' });
            
            const link = document.createElement('a');
            link.href = URL.createObjectURL(dataBlob);
            const exportDay = new Intl.DateTimeFormat('sv-SE', { timeZone: 'Asia/Shanghai' }).format(new Date());
            link.download = `azur-lane-skins-${exportDay}.json`;
            link.click();
            setTimeout(() => URL.revokeObjectURL(link.href), 1000);
        }

        function validateImportedData(data) {
            if (!data || typeof data !== 'object' || Array.isArray(data) || !Array.isArray(data.characters) || !Array.isArray(data.skins)) {
                throw new Error('characters 和 skins 必须是数组，请选择本工具导出的 JSON 备份');
            }
            const rarities = new Set(['ultra_rare', 'super_rare', 'elite', 'rare', 'common']);
            const characterIds = new Set();
            const skinIds = new Set();
            function requireRecord(value, label) {
                if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error(label + '必须是对象');
            }
            function requireId(value, label) {
                const id = typeof value === 'number' && Number.isSafeInteger(value) ? String(value) : value;
                if (typeof id !== 'string' || !/^[A-Za-z0-9_-]+$/.test(id)) throw new Error(label + '无效');
                return id;
            }
            function requireText(value, label, fallback = '') {
                if (value === undefined || value === null) return fallback;
                if (typeof value !== 'string') throw new Error(label + '必须是文本');
                return value;
            }
            function countValue(value, label, fallback = '0') {
                if (value === undefined || value === null || value === '') return fallback;
                if ((typeof value !== 'string' && typeof value !== 'number') || !Number.isSafeInteger(Number(value)) || Number(value) < 0) {
                    throw new Error(label + '必须是大于或等于 0 的整数');
                }
                return String(Number(value));
            }
            const nextCharacters = data.characters.map((record, index) => {
                const label = `第 ${index + 1} 个角色`;
                requireRecord(record, label);
                const id = requireId(record.id, label + ' ID');
                if (characterIds.has(id)) throw new Error('存在重复的角色 ID：' + id);
                characterIds.add(id);
                const name = requireText(record.name, label + '名称').trim();
                if (!name) throw new Error(label + '缺少名称');
                const rarity = record.rarity || 'common';
                if (!rarities.has(rarity)) throw new Error(label + '稀有度无效');
                return {
                    ...record, id, name, rarity,
                    acquisition: requireText(record.acquisition, label + '获取方式'),
                    collected: countValue(record.collected, label + '收集数量'),
                    shareableSkinCount: countValue(record.shareableSkinCount, label + '共享数量'),
                    customTargetCount: record.customTargetCount === undefined || record.customTargetCount === null || record.customTargetCount === ''
                        ? null : Number(countValue(record.customTargetCount, label + '目标数量'))
                };
            });
            const nextSkins = data.skins.map((record, index) => {
                const label = `第 ${index + 1} 件换装`;
                requireRecord(record, label);
                const id = requireId(record.id, label + ' ID');
                if (skinIds.has(id)) throw new Error('存在重复的换装 ID：' + id);
                skinIds.add(id);
                const characterId = requireId(record.characterId, label + '角色 ID');
                if (!characterIds.has(characterId)) throw new Error(label + '关联的角色不存在');
                const name = requireText(record.name, label + '名称').trim();
                if (!name) throw new Error(label + '缺少名称');
                if (record.owned !== undefined && typeof record.owned !== 'boolean') throw new Error(label + '拥有状态必须为 true 或 false');
                return { ...record, id, characterId, name, acquisition: requireText(record.acquisition, label + '获取方式'), owned: record.owned === true };
            });
            if (data.activityLog !== undefined) {
                if (!Array.isArray(data.activityLog)) throw new Error('活动记录必须是数组');
                data.activityLog.forEach((record, index) => {
                    requireRecord(record, `第 ${index + 1} 条活动记录`);
                    for (const key of ['type', 'target', 'name', 'characterName', 'oldName', 'logMessage', 'timestamp']) {
                        requireText(record[key], '活动记录的 ' + key);
                    }
                });
            }
            if (data.acquisitionSortOrder !== undefined && (!Array.isArray(data.acquisitionSortOrder) || data.acquisitionSortOrder.some(value => typeof value !== 'string'))) {
                throw new Error('获取方式排序必须是文本数组');
            }
            return { ...data, characters: nextCharacters, skins: nextSkins };
        }
        
        function importData() {
            const importFile = document.getElementById('import-file').files[0];
            const importText = document.getElementById('import-data').value.trim();
            
            if (!importFile && !importText) {
                alert('请选择文件或粘贴要导入的数据');
                return;
            }
            
            const processData = (dataStr) => {
                try {
                    const data = validateImportedData(JSON.parse(dataStr));
                    
                    const approved = window.confirmTrackerImport ? window.confirmTrackerImport(data) : confirm(`将导入 ${data.characters.length} 个角色、${data.skins.length} 件换装，并替换当前记录。导入后可撤销。\n\n确定继续吗？`);
                    if (approved) {
                        if (_saveDataTimer && !saveDataImmediate()) throw new Error('当前修改尚未保存，请先导出备份后再导入');
                        const snapshot = JSON.parse(JSON.stringify({ characters, skins, timestamp: new Date().toISOString() }));
                        const nextUndo = [snapshot, ...undoHistory].slice(0, 10);
                        const nextLog = data.activityLog ? data.activityLog.slice(0, 50) : activityLog;
                        const entries = [
                            ['azurLaneCharacters', JSON.stringify(data.characters)],
                            ['azurLaneSkins', JSON.stringify(data.skins)],
                            ['azurLaneActivityLog', JSON.stringify(nextLog)],
                            ['azurLaneUndoHistory', JSON.stringify(nextUndo)],
                            ['azurLaneRedoHistory', JSON.stringify([])],
                            ['azurLaneAnnouncementProcessed', null]
                        ];
                        if (data.acquisitionSortOrder) entries.push(['azurLaneAcquisitionSortOrder', JSON.stringify(data.acquisitionSortOrder)]);
                        notifySaveState('saving', '正在导入并保存…');
                        try {
                            writeStorageEntries(entries);
                        } catch (error) {
                            notifySaveState('error', storageErrorMessage(error));
                            throw error;
                        }
                        if (_saveUndoTimer) clearTimeout(_saveUndoTimer);
                        _saveUndoTimer = null;
                        characters = data.characters;
                        skins = data.skins;
                        activityLog = nextLog;
                        undoHistory = nextUndo;
                        redoHistory = [];
                        markSkinsIndexDirty();
                        if (!characters.some(character => character.id === currentCharacterId)) {
                            currentCharacterId = null;
                            characterDetail.classList.add('hidden');
                        }
                        updateUndoButton();
                        updateRedoButton();
                        notifySaveState('saved', '导入的数据已保存到此浏览器');
                        scheduleRender();
                        
                        importModal.classList.add('hidden');
                        document.getElementById('import-data').value = '';
                        document.getElementById('import-file').value = '';
                        
                        if (window.notifyTrackerMessage) window.notifyTrackerMessage('数据已导入并保存，可通过顶部撤销恢复。');
                        else alert('数据导入成功！');
                    }
                } catch (error) {
                    if (window.showTrackerImportError) window.showTrackerImportError(error.message);
                    else alert('导入失败：' + error.message);
                }
            };
            
            if (importFile) {
                const reader = new FileReader();
                reader.onload = (e) => {
                    processData(e.target.result);
                };
                reader.onerror = () => {
                    alert('读取文件失败');
                };
                reader.readAsText(importFile);
            } else {
                processData(importText);
            }
        }
        
        // ================= 快速录入：多栏结构化数据 =================
        const QUICK_ADD_RARITIES = [
            { value: 'common', label: '普通', color: '#94a3b8' },
            { value: 'rare', label: '稀有', color: '#3b82f6' },
            { value: 'elite', label: '精锐', color: '#8b5cf6' },
            { value: 'super_rare', label: '超稀有', color: '#f59e0b' },
            { value: 'ultra_rare', label: '海上传奇', color: '#06b6d4' }
        ];
        const QUICK_ADD_INITIAL_ROWS = 1;
        let quickAddRows = [];
        let quickAddRowSeq = 0;

        function nextQuickRowId() {
            quickAddRowSeq += 1;
            return 'qr-' + quickAddRowSeq + '-' + Date.now().toString(36);
        }

        function buildQuickAddRowHtml(row) {
            const rarityOptions = QUICK_ADD_RARITIES.map(r =>
                `<option value="${r.value}" ${row.rarity === r.value ? 'selected' : ''} style="color:${r.color};font-weight:600;">${r.label}</option>`
            ).join('');
            const nameInvalid = row.touched && !row.name ? 'border-red-400 ring-1 ring-red-300' : '';
            const rarityInvalid = row.touched && !row.rarity ? 'border-red-400 ring-1 ring-red-300' : '';
            return `
                <div class="quick-add-row grid grid-cols-1 md:grid-cols-12 gap-2 items-end p-3 border border-gray-200 rounded-lg bg-gray-50 hover:bg-white transition-colors" data-row-id="${row.id}">
                    <div class="md:col-span-3">
                        <label class="block text-xs text-gray-600 mb-1">舰船名称 <span class="text-red-500">*</span></label>
                        <input type="text" class="form-input py-1.5 text-sm quick-name ${nameInvalid}" value="${escapeHtml(row.name || '')}" placeholder="如：企业" aria-label="舰船名称" maxlength="40" />
                        <p class="quick-name-err text-xs text-red-600 mt-1 ${row.touched && !row.name ? '' : 'hidden'}">请填写舰船名称</p>
                    </div>
                    <div class="md:col-span-2">
                        <label class="block text-xs text-gray-600 mb-1">稀有度 <span class="text-red-500">*</span></label>
                        <select class="form-input py-1.5 text-sm quick-rarity ${rarityInvalid}" aria-label="稀有度">${rarityOptions}</select>
                    </div>
                    <div class="md:col-span-3">
                        <label class="block text-xs text-gray-600 mb-1">角色获取方式</label>
                        <input type="text" class="form-input py-1.5 text-sm quick-acquisition" value="${escapeHtml(row.acquisition || '')}" placeholder="如：建造、活动" aria-label="角色获取方式" maxlength="60" />
                    </div>
                    <div class="md:col-span-3">
                        <label class="block text-xs text-gray-600 mb-1">皮肤名称</label>
                        <input type="text" class="form-input py-1.5 text-sm quick-skin" value="${escapeHtml(row.skinText || '')}" placeholder="可空；多个用空格分隔" aria-label="皮肤名称" maxlength="120" />
                    </div>
                    <div class="md:col-span-1 flex justify-end">
                        <button type="button" class="btn btn-danger btn-sm quick-remove" aria-label="删除此录入栏" title="删除此栏">
                            <i class="fa fa-trash"></i>
                        </button>
                    </div>
                </div>
            `;
        }

        function renderQuickAddRows() {
            const container = document.getElementById('quick-add-rows');
            if (!container) return;
            const html = quickAddRows.map(buildQuickAddRowHtml).join('');
            container.innerHTML = html;
            updateQuickAddRowCount();
            applyQuickAddRarityColors();
        }

        function updateQuickAddRowCount() {
            const el = document.getElementById('quick-add-row-count');
            if (el) el.textContent = `共 ${quickAddRows.length} 栏`;
        }

        // 为每个 select 的选中项染色（避免 select 显示黑色）
        function applyQuickAddRarityColors() {
            const selects = document.querySelectorAll('#quick-add-rows .quick-rarity');
            selects.forEach(sel => {
                const opt = sel.options[sel.selectedIndex];
                const color = opt ? opt.style.color : '';
                sel.style.color = color || '';
                sel.style.fontWeight = '600';
            });
        }

        function escapeHtml(str) {
            return String(str)
                .replace(/&/g, '&amp;')
                .replace(/</g, '&lt;')
                .replace(/>/g, '&gt;')
                .replace(/"/g, '&quot;')
                .replace(/'/g, '&#39;');
        }

        function addQuickAddRow(data) {
            const row = {
                id: nextQuickRowId(),
                name: (data && data.name) || '',
                rarity: (data && data.rarity) || 'common',
                acquisition: (data && data.acquisition) || '',
                skinText: (data && data.skinText) || '',
                touched: false
            };
            quickAddRows.push(row);
            renderQuickAddRows();
            // 让新增栏的第一个输入框获焦
            requestAnimationFrame(() => {
                const el = document.querySelector(`.quick-add-row[data-row-id="${row.id}"] .quick-name`);
                if (el) el.focus();
            });
        }

        function removeQuickAddRow(rowId) {
            if (quickAddRows.length <= 1) {
                showQuickAddFeedback('error', '至少保留 1 个录入栏');
                return;
            }
            quickAddRows = quickAddRows.filter(r => r.id !== rowId);
            renderQuickAddRows();
        }

        function resetQuickAddRows() {
            quickAddRows = [];
            for (let i = 0; i < QUICK_ADD_INITIAL_ROWS; i++) addQuickAddRow();
            hideQuickAddFeedback();
        }

        function openQuickAddModal() {
            // 每次打开都重置为空白栏，避免残留上次输入
            resetQuickAddRows();
            quickAddModal.classList.remove('hidden');
            hideQuickAddFeedback();
        }

        function closeQuickAddModal() {
            quickAddModal.classList.add('hidden');
            // 关闭时清空所有栏，下次打开即为空白
            resetQuickAddRows();
        }

        function handleQuickAddRowClick(e) {
            const removeBtn = e.target.closest('.quick-remove');
            if (!removeBtn) return;
            const rowEl = e.target.closest('.quick-add-row');
            if (!rowEl) return;
            removeQuickAddRow(rowEl.dataset.rowId);
        }

        function handleQuickAddRowInput(e) {
            const target = e.target;
            const rowEl = target.closest('.quick-add-row');
            if (!rowEl) return;
            const rowId = rowEl.dataset.rowId;
            const row = quickAddRows.find(r => r.id === rowId);
            if (!row) return;
            row.touched = true;
            if (target.classList.contains('quick-name')) row.name = target.value;
            else if (target.classList.contains('quick-rarity')) row.rarity = target.value;
            else if (target.classList.contains('quick-acquisition')) row.acquisition = target.value;
            else if (target.classList.contains('quick-skin')) row.skinText = target.value;
            // 实时校验：只更新错误样式，不重渲染整栏避免输入焦点丢失
            validateQuickAddRowOnInput(rowEl);
            // 稀有度切换时同步颜色
            if (target.classList.contains('quick-rarity')) applyQuickAddRarityColors();
        }

        function validateQuickAddRowOnInput(rowEl) {
            if (!rowEl) return;
            const nameInput = rowEl.querySelector('.quick-name');
            const nameErr = rowEl.querySelector('.quick-name-err');
            const nameVal = (nameInput.value || '').trim();
            if (!nameVal) {
                nameInput.classList.add('border-red-400', 'ring-1', 'ring-red-300');
                if (nameErr) nameErr.classList.remove('hidden');
            } else {
                nameInput.classList.remove('border-red-400', 'ring-1', 'ring-red-300');
                if (nameErr) nameErr.classList.add('hidden');
            }
        }

        function validateAllQuickAddRows() {
            let valid = true;
            const rowEls = document.querySelectorAll('#quick-add-rows .quick-add-row');
            rowEls.forEach(el => {
                const nameInput = el.querySelector('.quick-name');
                const nameErr = el.querySelector('.quick-name-err');
                const nameVal = (nameInput.value || '').trim();
                const hasOtherContent = el.querySelector('.quick-acquisition').value.trim() || el.querySelector('.quick-skin').value.trim();
                if (!nameVal && hasOtherContent) {
                    valid = false;
                    nameInput.classList.add('border-red-400', 'ring-1', 'ring-red-300');
                    if (nameErr) nameErr.classList.remove('hidden');
                } else {
                    nameInput.classList.remove('border-red-400', 'ring-1', 'ring-red-300');
                    if (nameErr) nameErr.classList.add('hidden');
                }
            });
            return valid;
        }

        function collectQuickAddData() {
            const entries = [];
            const rowEls = document.querySelectorAll('#quick-add-rows .quick-add-row');
            rowEls.forEach(el => {
                const name = (el.querySelector('.quick-name').value || '').trim();
                const rarity = (el.querySelector('.quick-rarity').value || 'common').trim();
                const acquisition = (el.querySelector('.quick-acquisition').value || '').trim();
                const skinText = (el.querySelector('.quick-skin').value || '').trim();
                // 跳过完全空白的行
                if (!name && !acquisition && !skinText) return;
                const skinNames = skinText ? skinText.split(/\s+/).map(s => s.trim()).filter(s => s) : [];
                entries.push({ name, rarity, acquisition, skinNames });
            });
            return entries;
        }

        function showQuickAddFeedback(type, message) {
            const el = document.getElementById('quick-add-feedback');
            if (!el) return;
            const color = type === 'success' ? 'bg-green-50 text-green-700 border-green-200' :
                          type === 'error' ? 'bg-red-50 text-red-700 border-red-200' :
                          'bg-blue-50 text-blue-700 border-blue-200';
            const icon = type === 'success' ? 'fa-check-circle' :
                         type === 'error' ? 'fa-exclamation-circle' : 'fa-info-circle';
            el.className = `mt-3 px-3 py-2 rounded-lg border text-sm flex items-center ${color}`;
            el.innerHTML = `<i class="fa ${icon} mr-2"></i><span>${escapeHtml(message)}</span>`;
            el.classList.remove('hidden');
        }

        function hideQuickAddFeedback() {
            const el = document.getElementById('quick-add-feedback');
            if (el) { el.classList.add('hidden'); el.innerHTML = ''; }
        }

        function submitQuickAddRows() {
            // 标记所有栏为已触碰以便显示必填提示
            quickAddRows.forEach(r => r.touched = true);
            // 重新生成 DOM 以反映 touched 状态（罕见操作，可接受）
            renderQuickAddRows();
            if (!validateAllQuickAddRows()) {
                showQuickAddFeedback('error', '存在未填写舰船名称的录入栏，请补全后再提交。');
                return;
            }
            const entries = collectQuickAddData();
            if (entries.length === 0) {
                showQuickAddFeedback('error', '请至少填写一行有效数据。');
                return;
            }
            // 显示 loading 遮罩 + 禁用提交按钮，给浏览器一次重绘机会后再执行重活，消除卡顿感
            const submitBtn = document.getElementById('confirm-quick-add-btn');
            const cancelBtn = document.getElementById('cancel-quick-add-btn');
            submitBtn.disabled = true;
            cancelBtn.disabled = true;
            showQuickAddLoading();
            // 用双 RAF 确保 loading 已绘制到屏幕，再进入同步重活
            requestAnimationFrame(() => {
                requestAnimationFrame(() => {
                    try {
                        const result = quickAddData(entries);
                        if (result && result.success) {
                            closeQuickAddModal();
                        } else {
                            showQuickAddFeedback('error', (result && result.message) || '提交失败，请检查数据。');
                        }
                    } catch (err) {
                        console.error('快速录入提交出错:', err);
                        showQuickAddFeedback('error', '提交出错，请重试。');
                    } finally {
                        hideQuickAddLoading();
                        submitBtn.disabled = false;
                        cancelBtn.disabled = false;
                    }
                });
            });
        }

        function showQuickAddLoading() {
            const el = document.getElementById('quick-add-loading');
            if (el) el.classList.remove('hidden');
        }

        function hideQuickAddLoading() {
            const el = document.getElementById('quick-add-loading');
            if (el) el.classList.add('hidden');
        }

        // 核心写入：接收结构化数组 [{name, rarity, acquisition, skinNames[]}]
        function quickAddData(entries) {
            const timestamp = new Date().toISOString();
            saveUndoState();
            let charCount = 0, skinCount = 0;
            entries.forEach(entry => {
                if (!entry.name) return;
                const characterName = entry.name;
                const skinNames = entry.skinNames || [];
                let character = characters.find(char => char.name === characterName);
                let isNewCharacter = false;
                if (!character) {
                    character = {
                        id: Date.now().toString() + Math.random().toString(36).slice(2, 7),
                        name: characterName,
                        rarity: entry.rarity || 'common',
                        acquisition: entry.acquisition || '',
                        collected: '0',
                        shareableSkinCount: '0',
                        createdAt: timestamp,
                        updatedAt: timestamp
                    };
                    characters.push(character);
                    isNewCharacter = true;
                    charCount++;
                    addActivityLog({
                        type: 'add',
                        target: 'character',
                        id: character.id,
                        name: characterName,
                        timestamp: timestamp
                    });
                } else {
                    // 已存在角色：更新稀有度/获取方式
                    let updated = false;
                    if (entry.rarity && character.rarity !== entry.rarity) { character.rarity = entry.rarity; updated = true; }
                    if (entry.acquisition && character.acquisition !== entry.acquisition) { character.acquisition = entry.acquisition; updated = true; }
                    if (updated) character.updatedAt = timestamp;
                }
                skinNames.forEach(skinName => {
                    if (!skinName.trim()) return;
                    const existingSkin = skins.find(skin =>
                        skin.characterId === character.id && skin.name === skinName
                    );
                    if (!existingSkin) {
                        const newSkin = {
                            id: Date.now().toString() + Math.random().toString(36).slice(2, 11),
                            characterId: character.id,
                            name: skinName,
                            acquisition: '',
                            owned: false,
                            createdAt: timestamp,
                            updatedAt: timestamp
                        };
                        skins.push(newSkin);
                        skinCount++;
                        markSkinsIndexDirty();
                        // 如果角色设置了自定义目标收集数量，添加换装时自动+1
                        if (character.customTargetCount !== undefined && character.customTargetCount !== null && character.customTargetCount !== '') {
                            const oldTarget = Number(character.customTargetCount);
                            const newTarget = oldTarget + 1;
                            character.customTargetCount = newTarget;
                            character.updatedAt = timestamp;
                            addActivityLog({
                                type: 'update',
                                target: 'character',
                                id: character.id,
                                name: characterName,
                                logMessage: `新增换装"${skinName}"，自动调整目标收集数量：${oldTarget} → ${newTarget}`,
                                timestamp: timestamp
                            });
                        }
                        addActivityLog({
                            type: 'add',
                            target: 'skin',
                            id: newSkin.id,
                            name: skinName,
                            characterId: character.id,
                            characterName: characterName,
                            timestamp: timestamp
                        });
                    }
                });
            });
            saveData();
            scheduleRender();
            return { success: true, characters: charCount, skins: skinCount };
        }
        
        const announcementProcessedKey = 'azurLaneAnnouncementProcessed';

        // 只统一表现形式；跨语言名称必须由公告明确提供别名，不做模糊匹配。
        function announcementName(value) {
            return value.normalize('NFKC').replace(/\s+/g, '').replace(/[‐‑‒–—−]/g, '-').toLowerCase();
        }

        function readAnnouncementStorage(key, fallback) {
            const raw = localStorage.getItem(key);
            const value = raw === null ? fallback : JSON.parse(raw);
            if (!Array.isArray(value)) throw new Error('收藏存储格式不正确，请先导出备份检查：' + key);
            return structuredClone(value);
        }

        // 重绘保留可见记录的位置；新条目排在上方时仍让当前正在看的角色停在原处。
        function renderAnnouncementChanges() {
            const list = document.getElementById('character-list');
            const listTop = list.getBoundingClientRect().top;
            const anchor = [...list.querySelectorAll('.character-row')].find(row => row.getBoundingClientRect().bottom > listTop);
            const anchorId = anchor?.dataset.id;
            const anchorTop = anchor?.getBoundingClientRect().top;
            const elements = [list, document.querySelector('.detail-panel'), document.getElementById('acquisition-filter'), document.getElementById('detail-drawer-body')];
            const offsets = elements.map(element => [element.scrollLeft, element.scrollTop]);
            const page = [window.scrollX, window.scrollY];
            renderAll();
            elements.forEach((element, index) => { element.scrollLeft = offsets[index][0]; element.scrollTop = offsets[index][1]; });
            if (anchorId) {
                const restored = [...list.querySelectorAll('.character-row')].find(row => row.dataset.id === anchorId);
                if (restored) list.scrollTop += restored.getBoundingClientRect().top - anchorTop;
            }
            window.scrollTo({left: page[0], top: page[1], behavior: 'instant'});
        }

        function applyAnnouncementCollection(entries) {
            if (!Array.isArray(entries)) throw new Error('公告收藏条目必须是数组');
            if (_dataDirty && !saveDataImmediate()) throw new Error('当前编辑尚未保存，暂不追加公告收藏');
            flushPendingSaves();
            // 进入同源锁后重新读存储，防止另一个实例/标签页刚完成的追加被旧内存覆盖。
            const baseCharacters = readAnnouncementStorage('azurLaneCharacters', characters);
            const baseSkins = readAnnouncementStorage('azurLaneSkins', skins);
            validateImportedData({characters: baseCharacters, skins: baseSkins});
            const baseUndo = readAnnouncementStorage('azurLaneUndoHistory', undoHistory);
            const baseRedo = readAnnouncementStorage('azurLaneRedoHistory', redoHistory);
            const baseLog = readAnnouncementStorage('azurLaneActivityLog', activityLog);
            const previousProcessed = readAnnouncementStorage(announcementProcessedKey, []);
            const processed = new Set(previousProcessed);
            const nextCharacters = structuredClone(baseCharacters), nextSkins = structuredClone(baseSkins);
            const timestamp = new Date().toISOString();
            const result = {characters: 0, skins: 0};
            for (const entry of entries) {
                if (!entry || typeof entry.name !== 'string' || !entry.name.trim() || !Array.isArray(entry.aliases) || entry.aliases.some(alias => typeof alias !== 'string') || !Array.isArray(entry.skins)) throw new Error('公告收藏条目不完整');
                if (!['ultra_rare', 'super_rare', 'elite', 'rare', 'common'].includes(entry.rarity) || typeof entry.acquisition !== 'string') throw new Error('公告角色信息无效');
                const names = [...new Set([entry.name, ...entry.aliases].map(announcementName).filter(Boolean))];
                const characterKeys = names.map(name => JSON.stringify(['character', name]));
                const matches = nextCharacters.filter(character => names.includes(announcementName(character.name)));
                if (matches.length > 1) throw new Error('公告别名匹配到多个角色，请先整理重复档案：' + entry.name);
                let character = matches[0];
                if (!character && !characterKeys.some(key => processed.has(key))) {
                    character = {id: 'announcement_' + Date.now().toString(36) + '_' + Math.random().toString(36).slice(2), name: entry.name.trim(), rarity: entry.rarity, acquisition: entry.acquisition,
                        collected: '0', shareableSkinCount: '0', customTargetCount: null, createdAt: timestamp, updatedAt: timestamp};
                    nextCharacters.push(character);
                    result.characters++;
                }
                characterKeys.forEach(key => processed.add(key));
                for (const skin of entry.skins) {
                    if (!skin || typeof skin.name !== 'string' || !skin.name.trim() || typeof skin.acquisition !== 'string') throw new Error('公告换装信息无效');
                    const name = announcementName(skin.name);
                    const keys = names.map(characterName => JSON.stringify(['skin', characterName, name]));
                    if (character && !keys.some(key => processed.has(key)) && !nextSkins.some(existing => existing.characterId === character.id && announcementName(existing.name) === name)) {
                        nextSkins.push({id: 'announcement_' + Date.now().toString(36) + '_' + Math.random().toString(36).slice(2), characterId: character.id, name: skin.name.trim(), acquisition: skin.acquisition,
                            owned: false, createdAt: timestamp, updatedAt: timestamp});
                        if (character.customTargetCount !== undefined && character.customTargetCount !== null && character.customTargetCount !== '') character.customTargetCount = Number(character.customTargetCount) + 1;
                        result.skins++;
                    }
                    // 撤销或手动删除后不再次导入同一条；下一次真正的新换装仍可追加。
                    keys.forEach(key => processed.add(key));
                }
            }
            const changed = result.characters + result.skins > 0;
            const nextUndo = changed ? [{characters: baseCharacters, skins: baseSkins, timestamp}, ...baseUndo].slice(0, 10) : baseUndo;
            const nextLog = changed ? [{type: 'sync', target: 'action', logMessage: '公告同步：新增 ' + result.characters + ' 位角色、' + result.skins + ' 件换装', timestamp}, ...baseLog].slice(0, 50) : baseLog;
            const writes = [];
            if (processed.size !== previousProcessed.length) writes.push([announcementProcessedKey, JSON.stringify([...processed])]);
            if (changed) writes.push(
                ['azurLaneCharacters', JSON.stringify(nextCharacters)], ['azurLaneSkins', JSON.stringify(nextSkins)],
                ['azurLaneUndoHistory', JSON.stringify(nextUndo)], ['azurLaneRedoHistory', JSON.stringify([])], ['azurLaneActivityLog', JSON.stringify(nextLog)]
            );
            // 数据、撤销、日志和处理标记共用原有回滚事务，成功前不修改内存或界面。
            try { if (writes.length) writeStorageEntries(writes); }
            catch (error) { notifySaveState('error', storageErrorMessage(error)); throw error; }
            const refresh = changed || JSON.stringify(characters) !== JSON.stringify(nextCharacters) || JSON.stringify(skins) !== JSON.stringify(nextSkins);
            characters = nextCharacters; skins = nextSkins; undoHistory = nextUndo; redoHistory = changed ? [] : baseRedo; activityLog = nextLog;
            if (refresh) { markSkinsIndexDirty(); renderAnnouncementChanges(); }
            updateUndoButton(); updateRedoButton();
            if (changed) notifySaveState('saved', nextLog[0].logMessage);
            return result;
        }

        window.syncAnnouncementCollection = entries => navigator.locks
            ? navigator.locks.request('azurLaneAnnouncementCollection', () => applyAnnouncementCollection(entries))
            : Promise.resolve().then(() => applyAnnouncementCollection(entries));

        // 其他标签页落盘后刷新无待保存编辑的页面；本地正在编辑时由下次同步先提交。
        window.addEventListener('storage', event => {
            if (!['azurLaneCharacters', 'azurLaneSkins', 'azurLaneUndoHistory', 'azurLaneRedoHistory'].includes(event.key) || _dataDirty || _saveUndoTimer) return;
            window.syncAnnouncementCollection([]).catch(error => notifySaveState('error', storageErrorMessage(error)));
        });

        function deleteCharacter(characterId) {
            if (!characterId) return;
            
            const character = characters.find(char => char.id === characterId);
            if (!character) return;
            
            if (confirm(`确定要删除角色"${character.name}"及其所有换装记录吗？`)) {
                saveUndoState();
                
                const characterSkins = getSkinsByCharacter(characterId);
                
                skins = skins.filter(skin => skin.characterId !== characterId);
                markSkinsIndexDirty();
                characters = characters.filter(char => char.id !== characterId);
                
                addActivityLog({
                    type: 'delete',
                    target: 'character',
                    id: characterId,
                    name: character.name,
                    skinsCount: characterSkins.length,
                    timestamp: new Date().toISOString()
                });
                
                saveData();
                scheduleRender();
                
                if (currentCharacterId === characterId) {
                    characterDetail.classList.add('hidden');
                    currentCharacterId = null;
                }
            }
        }
        
        function addActivityLog(log) {
            try {
                activityLog.unshift(log);
                
                if (activityLog.length > 50) {
                    activityLog = activityLog.slice(0, 50);
                }
                
                localStorage.setItem('azurLaneActivityLog', JSON.stringify(activityLog));
            } catch (error) {
                console.error('保存活动日志失败:', error);
            }
        }
        
        function renderRecentActivity() {
            const recentActivityElement = document.getElementById('recent-activity');
            const recentActivityDropdown = document.getElementById('recent-activity-dropdown');
            
            if (activityLog.length === 0) {
                recentActivityElement.innerHTML = '<span class="text-gray-500">暂无活动记录</span>';
                recentActivityDropdown.innerHTML = '<div class="p-4 text-center text-gray-500">暂无活动记录</div>';
                return;
            }
            
            const latestActivity = activityLog[0];
            recentActivityElement.innerHTML = `
                <span class="cursor-pointer hover:text-blue-200 flex items-center">
                    <i class="fa fa-clock-o mr-2"></i>
                    ${escapeHtml(formatActivityLog(latestActivity, true))}
                    <i class="fa fa-chevron-down ml-2"></i>
                </span>
            `;
            
            recentActivityDropdown.innerHTML = '';
            const recentLogs = activityLog.slice(0, 50);
            
            recentLogs.forEach(log => {
                const logElement = document.createElement('div');
                logElement.className = 'p-3 border-b border-gray-100 hover:bg-blue-50';
                logElement.innerHTML = `
                    <div class="flex items-start">
                        <div class="mr-3 mt-1">
                            <i class="fa ${getActivityIcon(log)} text-blue-500"></i>
                        </div>
                        <div>
                            <div class="text-black">${escapeHtml(formatActivityLog(log))}</div>
                            <div class="text-xs text-gray-500 mt-1">${formatDateTime(log.timestamp)}</div>
                        </div>
                    </div>
                `;
                recentActivityDropdown.appendChild(logElement);
            });
        }
        
        function formatActivityLog(log, isShort = false) {
            const { type, target, name, characterName, logMessage, skinsCount, oldName } = log;
            
            if (target === 'character') {
                switch (type) {
                    case 'add':
                        return isShort ? `添加了角色"${name}"` : `添加了角色"${name}"`;
                    case 'update':
                        if (logMessage) {
                            if (logMessage.includes('更新收集进度')) {
                                const oldCount = logMessage.match(/从 (\d+)\//);
                                const newCount = logMessage.match(/到 (\d+)\//);
                                if (oldCount && newCount) {
                                    const oldVal = parseInt(oldCount[1], 10);
                                    const newVal = parseInt(newCount[1], 10);
                                    const action = newVal > oldVal ? '增加' : (newVal < oldVal ? '减少' : '更新');
                                    return isShort ? `${action}了"${name}"的收集进度` : `${action}了角色"${name}"的收集进度：${oldVal} → ${newVal}`;
                                }
                                return isShort ? `更新了"${name}"的收集进度` : `更新了角色"${name}"的收集进度`;
                            }
                            return isShort ? `更新了角色"${name}"` : `更新了角色"${name}": ${logMessage}`;
                        }
                        if (oldName && oldName !== name) {
                            return isShort ? `将角色"${oldName}"重命名为"${name}"` : `将角色"${oldName}"重命名为"${name}"`;
                        }
                        return isShort ? `更新了角色"${name}"` : `更新了角色"${name}"的信息`;
                    case 'delete':
                        return isShort ? `删除了角色"${name}"` : `删除了角色"${name}"及${skinsCount || 0}个换装`;
                    default:
                        return `对角色"${name}"进行了操作`;
                }
            } else if (target === 'skin') {
                switch (type) {
                    case 'add':
                        return isShort ? `为"${characterName}"添加了换装"${name}"` : `为角色"${characterName}"添加了换装"${name}"`;
                    case 'update':
                        if (logMessage) {
                            return isShort ? `更新了"${characterName}"的换装"${name}"` : `更新了"${characterName}"的换装"${name}": ${logMessage}`;
                        }
                        return isShort ? `更新了"${characterName}"的换装"${name}"` : `更新了"${characterName}"的换装"${name}"的信息`;
                    case 'delete':
                        return isShort ? `删除了换装"${name}"` : `删除了"${characterName}"的换装"${name}"`;
                    default:
                        return `对换装"${name}"进行了操作`;
                }
            } else if (target === 'action') {
                switch (type) {
                    case 'sync':
                        return logMessage || '同步了公告收藏';
                    case 'undo':
                        return isShort ? '撤销了最近操作' : '撤销了最近的操作';
                    default:
                        return '执行了操作';
                }
            }
            
            return `执行了操作`;
        }
        
        function getActivityIcon(log) {
            const { type, target } = log;
            
            if (target === 'character') {
                switch (type) {
                    case 'add': return 'fa-plus-circle';
                    case 'update': return 'fa-pencil';
                    case 'delete': return 'fa-trash';
                    default: return 'fa-info-circle';
                }
            } else if (target === 'skin') {
                switch (type) {
                    case 'add': return 'fa-plus-square';
                    case 'update': return 'fa-pencil-square';
                    case 'delete': return 'fa-trash';
                    default: return 'fa-info-circle';
                }
            }
            
            return 'fa-info-circle';
        }
        
        function formatDateTime(dateTimeString) {
            const date = new Date(dateTimeString);
            return date.toLocaleDateString('zh-CN', { 
                year: 'numeric', 
                month: '2-digit', 
                day: '2-digit', 
                hour: '2-digit', 
                minute: '2-digit', 
                second: '2-digit' 
            });
        }
        
        function calculateUncollectedValue() {
            let totalValue = 0;
            
            skins.forEach(skin => {
                if (!skin.owned && skin.acquisition) {
                    const price = parseInt(skin.acquisition.trim(), 10);
                    if (!isNaN(price)) {
                        totalValue += price;
                    }
                }
            });
            
            return totalValue;
        }
        
        function updateUncollectedValueDisplay() {
            const totalValue = calculateUncollectedValue();
            const valueElement = document.getElementById('uncollected-value');
            if (valueElement) {
                const spanElement = valueElement.querySelector('span');
                if (spanElement) {
                    spanElement.textContent = totalValue.toLocaleString();
                }
                valueElement.classList.remove('hidden');
            }
        }
        
        function saveData() {
            _dataDirty = true;
            if (_saveDataTimer) clearTimeout(_saveDataTimer);
            notifySaveState('saving', '正在保存修改…');
            _saveDataTimer = setTimeout(saveDataImmediate, 300);
        }

        function saveDataImmediate() {
            if (_saveDataTimer) clearTimeout(_saveDataTimer);
            _saveDataTimer = null;
            try {
                writeStorageEntries([
                    ['azurLaneCharacters', JSON.stringify(characters)],
                    ['azurLaneSkins', JSON.stringify(skins)]
                ]);
                _dataDirty = false;
                notifySaveState('saved', '修改已保存到此浏览器');
                return true;
            } catch (error) {
                console.error('保存数据失败:', error);
                notifySaveState('error', storageErrorMessage(error));
                return false;
            }
        }

        function flushPendingSaves() {
            if (_saveDataTimer) {
                clearTimeout(_saveDataTimer);
                _saveDataTimer = null;
                saveDataImmediate();
            }
            if (_saveUndoTimer) {
                clearTimeout(_saveUndoTimer);
                _saveUndoTimer = null;
                saveUndoPersistent();
            }
        }

        window.addEventListener('beforeunload', flushPendingSaves);
        window.addEventListener('pagehide', flushPendingSaves);
        document.addEventListener('visibilitychange', () => {
            if (document.visibilityState === 'hidden') flushPendingSaves();
        });
        
        function saveUndoState() {
            try {
                const undoState = {
                    characters: characters.map(char => ({
                        id: char.id,
                        name: char.name,
                        rarity: char.rarity,
                        acquisition: char.acquisition,
                        collected: char.collected,
                        customTargetCount: char.customTargetCount !== undefined ? char.customTargetCount : null,
                        shareableSkinCount: char.shareableSkinCount !== undefined ? char.shareableSkinCount : '0',
                        createdAt: char.createdAt,
                        updatedAt: char.updatedAt
                    })),
                    skins: skins.map(skin => ({
                        id: skin.id,
                        characterId: skin.characterId,
                        name: skin.name,
                        acquisition: skin.acquisition,
                        owned: skin.owned,
                        createdAt: skin.createdAt,
                        updatedAt: skin.updatedAt
                    })),
                    timestamp: new Date().toISOString()
                };

                undoHistory.unshift(undoState);

                if (undoHistory.length > 10) {
                    undoHistory = undoHistory.slice(0, 10);
                }

                redoHistory = [];

                updateUndoButton();
                updateRedoButton();

                // 防抖持久化到 localStorage
                if (_saveUndoTimer) clearTimeout(_saveUndoTimer);
                _saveUndoTimer = setTimeout(saveUndoPersistent, 300);
            } catch (error) {
                console.error('保存撤销状态失败:', error);
            }
        }

        function saveUndoPersistent() {
            _saveUndoTimer = null;
            try {
                writeStorageEntries([
                    ['azurLaneRedoHistory', null],
                    ['azurLaneUndoHistory', JSON.stringify(undoHistory)]
                ]);
            } catch (storageError) {
                if (storageError.name === 'QuotaExceededError') {
                    undoHistory = undoHistory.slice(0, 3);
                    try {
                        writeStorageEntries([
                            ['azurLaneRedoHistory', null],
                            ['azurLaneUndoHistory', JSON.stringify(undoHistory)]
                        ]);
                        console.warn('撤销历史已清理以节省存储空间');
                    } catch (e) {
                        console.error('清空历史失败:', e);
                    }
                }
            }
        }
        
        function undoLastAction() {
            flushPendingSaves();
            if (undoHistory.length === 0) {
                alert('没有可撤销的操作');
                return;
            }
            
            let operationContent = '最近的操作';
            if (activityLog.length > 0) {
                const recentLog = activityLog[0];
                if (recentLog.type !== 'undo' && recentLog.type !== 'redo') {
                    operationContent = formatActivityLog(recentLog, false);
                }
            }
            
            { // Undo is reversible through the adjacent redo action.
                const currentState = {
                    characters: characters.map(char => ({
                        id: char.id,
                        name: char.name,
                        rarity: char.rarity,
                        acquisition: char.acquisition,
                        collected: char.collected,
                        customTargetCount: char.customTargetCount !== undefined ? char.customTargetCount : null,
                        shareableSkinCount: char.shareableSkinCount !== undefined ? char.shareableSkinCount : '0',
                        createdAt: char.createdAt,
                        updatedAt: char.updatedAt
                    })),
                    skins: skins.map(skin => ({
                        id: skin.id,
                        characterId: skin.characterId,
                        name: skin.name,
                        acquisition: skin.acquisition,
                        owned: skin.owned,
                        createdAt: skin.createdAt,
                        updatedAt: skin.updatedAt
                    })),
                    timestamp: new Date().toISOString()
                };
                
                redoHistory.unshift(currentState);
                
                if (redoHistory.length > 20) {
                    redoHistory = redoHistory.slice(0, 20);
                }
                
                const previousState = undoHistory.shift();
                characters = previousState.characters;
                skins = previousState.skins;
                markSkinsIndexDirty();
                
                saveData();
                persistUndoRedoHistory();
                
                scheduleRender();
                updateUndoButton();
                updateRedoButton();
                
                addActivityLog({
                    type: 'undo',
                    target: 'action',
                    timestamp: new Date().toISOString()
                });
            }
        }
        
        function redoLastAction() {
            flushPendingSaves();
            if (redoHistory.length === 0) {
                alert('没有可重做的操作');
                return;
            }
            
            let operationContent = '上一次撤销的操作';
            for (let i = 0; i < activityLog.length; i++) {
                const log = activityLog[i];
                if (log.type === 'undo') {
                    for (let j = i + 1; j < activityLog.length; j++) {
                        const prevLog = activityLog[j];
                        if (prevLog.type !== 'undo' && prevLog.type !== 'redo') {
                            operationContent = formatActivityLog(prevLog, false);
                            break;
                        }
                    }
                    break;
                }
            }
            
            { // Redo can itself be undone without another confirmation dialog.
                const currentState = {
                    characters: characters.map(char => ({
                        id: char.id,
                        name: char.name,
                        rarity: char.rarity,
                        acquisition: char.acquisition,
                        collected: char.collected,
                        customTargetCount: char.customTargetCount !== undefined ? char.customTargetCount : null,
                        shareableSkinCount: char.shareableSkinCount !== undefined ? char.shareableSkinCount : '0',
                        createdAt: char.createdAt,
                        updatedAt: char.updatedAt
                    })),
                    skins: skins.map(skin => ({
                        id: skin.id,
                        characterId: skin.characterId,
                        name: skin.name,
                        acquisition: skin.acquisition,
                        owned: skin.owned,
                        createdAt: skin.createdAt,
                        updatedAt: skin.updatedAt
                    })),
                    timestamp: new Date().toISOString()
                };
                
                undoHistory.unshift(currentState);
                
                if (undoHistory.length > 20) {
                    undoHistory = undoHistory.slice(0, 20);
                }
                
                const nextState = redoHistory.shift();
                characters = nextState.characters;
                skins = nextState.skins;
                markSkinsIndexDirty();
                
                saveData();
                persistUndoRedoHistory();
                
                scheduleRender();
                updateUndoButton();
                updateRedoButton();
                
                addActivityLog({
                    type: 'redo',
                    target: 'action',
                    timestamp: new Date().toISOString()
                });
            }
        }
        
        function persistUndoRedoHistory() {
            try {
                writeStorageEntries([
                    ['azurLaneUndoHistory', JSON.stringify(undoHistory)],
                    ['azurLaneRedoHistory', JSON.stringify(redoHistory)]
                ]);
                return true;
            } catch (error) {
                console.error('保存撤销记录失败:', error);
                notifySaveState('error', '本次修改仍在页面中，但撤销记录未能保存。请导出备份。');
                return false;
            }
        }

        function updateUndoButton() {
            const undoBtn = document.getElementById('undo-btn');
            if (undoHistory.length > 0) {
                undoBtn.disabled = false;
                undoBtn.classList.remove('opacity-50', 'cursor-not-allowed');
            } else {
                undoBtn.disabled = true;
                undoBtn.classList.add('opacity-50', 'cursor-not-allowed');
            }
        }
        
        function updateRedoButton() {
            const redoBtn = document.getElementById('redo-btn');
            if (redoHistory.length > 0) {
                redoBtn.disabled = false;
                redoBtn.classList.remove('opacity-50', 'cursor-not-allowed');
            } else {
                redoBtn.disabled = true;
                redoBtn.classList.add('opacity-50', 'cursor-not-allowed');
            }
        }
        
        function getRarityText(rarity) {
            const rarityMap = {
                common: '普通',
                rare: '稀有',
                elite: '精锐',
                super_rare: '超稀有',
                ultra_rare: '海上传奇'
            };
            
            return rarityMap[rarity] || rarity;
        }
        
        function isCollaborationCharacter(character) {
            const acquisition = character.acquisition || '';
            return acquisition.includes('联动');
        }
        
        function getTargetCount(character) {
            // 优先使用用户自定义的目标收集数量
            if (character.customTargetCount !== undefined && character.customTargetCount !== null && character.customTargetCount !== '') {
                const customValue = Number(character.customTargetCount);
                if (Number.isInteger(customValue) && customValue >= 0) {
                    return customValue;
                }
            }
            
            // 默认计算规则
            const totalCount = getSkinsByCharacter(character.id).length;
            if (isCollaborationCharacter(character)) {
                return totalCount;
            }
            return totalCount + 1;
        }

        // 获取角色的现有收集数量（= 原逻辑数量 + 可共享换装角色数量）
        function getEffectiveCollected(character) {
            return (parseInt(character.collected, 10) || 0) + (parseInt(character.shareableSkinCount, 10) || 0);
        }

        // 判断角色是否已独立完成收集（原逻辑数量 >= 目标数量）
        function isCharacterCompleted(character) {
            return (parseInt(character.collected, 10) || 0) >= getTargetCount(character);
        }

        // 判断角色是否仅通过可共享换装达到目标（原逻辑数量 < 目标数量，但现有数量 >= 目标数量）
        function isCharacterCompletedViaShareable(character) {
            const rawCollected = parseInt(character.collected, 10) || 0;
            const targetCount = getTargetCount(character);
            return rawCollected < targetCount && getEffectiveCollected(character) >= targetCount;
        }
        
        // 获取字段的中文显示名
        function getFieldName(field) {
            const nameMap = {
                'shareableSkinCount': '可共享换装角色数量'
            };
            return nameMap[field] || field;
        }
        
        // 修改额外计数（增减按钮）
        function changeExtraCount(characterId, field, change) {
            const character = characters.find(char => char.id === characterId);
            if (character) {
                const currentValue = parseInt(character[field], 10) || 0;
                const newValue = Math.max(0, currentValue + change);
                
                if (newValue !== currentValue) {
                    saveUndoState();
                    
                    const fieldName = getFieldName(field);
                    const oldValue = currentValue;
                    character[field] = newValue.toString();
                    character.updatedAt = new Date().toISOString();
                    
                    addActivityLog({
                        type: 'update',
                        target: 'character',
                        id: character.id,
                        name: character.name,
                        logMessage: `更新${fieldName}：${oldValue} → ${newValue}`,
                        timestamp: character.updatedAt
                    });
                    
                    saveData();
                    scheduleRender();
                }
            }
        }
        
        // 修改额外计数（输入框直接输入）
        function updateExtraCountFromInput(characterId, field, inputValue) {
            const character = characters.find(char => char.id === characterId);
            if (!character) return;
            
            let newCount = parseInt(inputValue, 10);
            if (isNaN(newCount)) {
                // 无效输入，还原
                const inputs = document.querySelectorAll(`input[data-character-id="${characterId}"][data-field="${field}"]`);
                inputs.forEach(input => {
                    input.value = character[field] || '0';
                });
                return;
            }
            
            newCount = Math.max(0, newCount);
            const oldValue = parseInt(character[field], 10) || 0;
            
            if (newCount !== oldValue) {
                saveUndoState();
                
                const fieldName = getFieldName(field);
                character[field] = newCount.toString();
                character.updatedAt = new Date().toISOString();
                
                addActivityLog({
                    type: 'update',
                    target: 'character',
                    id: character.id,
                    name: character.name,
                    logMessage: `更新${fieldName}：${oldValue} → ${newCount}`,
                    timestamp: character.updatedAt
                });
                
                saveData();
                scheduleRender();
            }
        }
    
