
"""
三国杀游戏引擎 - 核心逻辑
"""
import random
import copy
from enum import Enum
from dataclasses import dataclass, field
from typing import Optional


class CardType(Enum):
    SHA = "杀"
    SHAN = "闪"
    TAO = "桃"
    GUOHE = "过河拆桥"
    SHUNSHOU = "顺手牵羊"
    WUZHONG = "无中生有"
    JUEDOU = "决斗"
    NANMAN = "南蛮入侵"
    WANJIAN = "万箭齐发"
    WUXIE = "无懈可击"
    WUGU = "五谷丰登"
    TAOYUAN = "桃园结义"
    BINGLIANG = "兵粮寸断"
    LE_BU_SI_SHU = "乐不思蜀"
    SHAN_DIAN = "闪电"
    ZHUGE = "诸葛连弩"
    QINGLONG = "青龙偃月刀"
    ZHANGBA = "丈八蛇矛"
    BAGUA = "八卦阵"
    CHITU = "赤兔"
    DILU = "的卢"


class CardSuit(Enum):
    SPADE = "\u2660"
    HEART = "\u2665"
    CLUB = "\u2663"
    DIAMOND = "\u2666"


class CardCategory(Enum):
    BASIC = "基本牌"
    STRATEGY = "锦囊牌"
    EQUIPMENT = "装备牌"


@dataclass
class Card:
    id: int
    name: str
    card_type: CardType
    suit: CardSuit
    number: int
    category: CardCategory

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "type": self.card_type.name,
            "suit": self.suit.value,
            "number": self.number,
            "category": self.category.value
        }


@dataclass
class Hero:
    name: str
    max_hp: int
    kingdom: str
    skills: list = field(default_factory=list)
    skill_descriptions: list = field(default_factory=list)

    def to_dict(self):
        return {
            "name": self.name,
            "max_hp": self.max_hp,
            "kingdom": self.kingdom,
            "skills": self.skill_descriptions
        }


class Player:
    def __init__(self, player_id: str, name: str, hero: Hero, is_ai: bool = False, ai_level: str = "medium"):
        self.player_id = player_id
        self.name = name
        self.hero = hero
        self.hp = hero.max_hp
        self.max_hp = hero.max_hp
        self.hand_cards: list[Card] = []
        self.equipment: dict[str, Optional[Card]] = {
            "weapon": None, "armor": None, "mount_plus": None, "mount_minus": None
        }
        self.is_alive = True
        self.is_ai = is_ai
        self.ai_level = ai_level
        self.skip_draw = False
        self.skip_play = False
        self.has_played_sha = False
        self.sha_count_limit = 1
        self.judge_cards: list[Card] = []

    def draw_cards(self, game, count: int):
        for _ in range(count):
            card = game.draw_card()
            if card:
                self.hand_cards.append(card)

    def lose_hp(self, game, amount: int, source=None):
        self.hp -= amount
        if self.hp <= 0:
            self.hp = 0
            if not self._try_save_with_tao(game):
                self.is_alive = False

    def recover_hp(self, amount: int):
        self.hp = min(self.hp + amount, self.max_hp)

    def _try_save_with_tao(self, game) -> bool:
        for card in self.hand_cards:
            if card.card_type == CardType.TAO:
                self.hand_cards.remove(card)
                game.discard_pile.append(card)
                self.hp = 1
                return True
        return False

    def get_attack_range(self) -> int:
        r = 1
        if self.equipment.get("weapon"):
            weapon = self.equipment["weapon"]
            if weapon.card_type in [CardType.QINGLONG, CardType.ZHANGBA]:
                r = 3
        return r

    def get_distance_to(self, game, target) -> int:
        alive = game.get_alive_players()
        my_idx = alive.index(self)
        target_idx = alive.index(target)
        clockwise = (target_idx - my_idx) % len(alive)
        counter_clockwise = (my_idx - target_idx) % len(alive)
        dist = min(clockwise, counter_clockwise)
        if target.equipment.get("mount_plus"):
            dist += 1
        if self.equipment.get("mount_minus"):
            dist -= 1
        return max(1, dist)

    def to_dict(self, hide_hand: bool = True):
        return {
            "player_id": self.player_id,
            "name": self.name,
            "hero": self.hero.to_dict(),
            "hp": self.hp,
            "max_hp": self.max_hp,
            "hand_count": len(self.hand_cards),
            "hand_cards": [c.to_dict() for c in self.hand_cards] if not hide_hand else [],
            "equipment": {k: v.to_dict() if v else None for k, v in self.equipment.items()},
            "is_alive": self.is_alive,
            "is_ai": self.is_ai,
            "ai_level": self.ai_level,
            "skip_draw": self.skip_draw,
            "skip_play": self.skip_play,
            "judge_count": len(self.judge_cards),
            "attack_range": self.get_attack_range()
        }


class GamePhase(Enum):
    WAITING = "waiting"
    JUDGE = "judge"
    DRAW = "draw"
    PLAY = "play"
    DISCARD = "discard"
    GAME_OVER = "game_over"


class Game:
    def __init__(self, room_id: str):
        self.room_id = room_id
        self.players: list[Player] = []
        self.draw_pile: list[Card] = []
        self.discard_pile: list[Card] = []
        self.current_player_idx = 0
        self.phase = GamePhase.WAITING
        self.turn_count = 0
        self.log: list[str] = []
        self.max_players = 8
        self.min_players = 2
        self.card_id_counter = 0
        self.game_started = False
        self.pending_action = None
        self.action_queue: list = []
        self._init_deck()

    def _init_deck(self):
        suits = [CardSuit.SPADE, CardSuit.HEART, CardSuit.CLUB, CardSuit.DIAMOND]
        cards_def = [
            (CardType.SHA, [2,3,4,5,6,7,8,9,10], [CardSuit.SPADE, CardSuit.CLUB, CardSuit.DIAMOND], 0),
            (CardType.SHA, [10], [CardSuit.HEART], 0),
            (CardType.SHAN, [2,3,4,5,6,7,8,9,10], [CardSuit.HEART, CardSuit.DIAMOND], 0),
            (CardType.TAO, [2,3,4,5,6,7,8,9], [CardSuit.HEART], 0),
            (CardType.TAO, [2,3,4,5,6,7,8,9], [CardSuit.DIAMOND], 0),
            (CardType.GUOHE, [3,4], [CardSuit.SPADE], 1),
            (CardType.GUOHE, [3,4], [CardSuit.CLUB], 1),
            (CardType.SHUNSHOU, [3,4], [CardSuit.SPADE], 1),
            (CardType.SHUNSHOU, [3,4], [CardSuit.DIAMOND], 1),
            (CardType.WUZHONG, [7,8,9], [CardSuit.HEART], 1),
            (CardType.JUEDOU, [1], [CardSuit.SPADE], 1),
            (CardType.JUEDOU, [1], [CardSuit.CLUB], 1),
            (CardType.JUEDOU, [1], [CardSuit.DIAMOND], 1),
            (CardType.NANMAN, [7], [CardSuit.SPADE], 1),
            (CardType.NANMAN, [7], [CardSuit.CLUB], 1),
            (CardType.WANJIAN, [1], [CardSuit.HEART], 1),
            (CardType.WUXIE, [11,12,13], [CardSuit.SPADE, CardSuit.CLUB], 1),
            (CardType.WUGU, [3,4], [CardSuit.HEART], 1),
            (CardType.TAOYUAN, [1], [CardSuit.HEART], 1),
            (CardType.BINGLIANG, [10], [CardSuit.SPADE], 1),
            (CardType.BINGLIANG, [10], [CardSuit.CLUB], 1),
            (CardType.LE_BU_SI_SHU, [6], [CardSuit.HEART], 1),
            (CardType.LE_BU_SI_SHU, [6], [CardSuit.SPADE], 1),
            (CardType.LE_BU_SI_SHU, [6], [CardSuit.CLUB], 1),
            (CardType.SHAN_DIAN, [1], [CardSuit.SPADE], 1),
            (CardType.SHAN_DIAN, [1], [CardSuit.HEART], 1),
            (CardType.ZHUGE, [1], [CardSuit.SPADE, CardSuit.CLUB], 1),
            (CardType.QINGLONG, [5], [CardSuit.SPADE], 1),
            (CardType.ZHANGBA, [12], [CardSuit.SPADE], 1),
            (CardType.BAGUA, [2], [CardSuit.SPADE, CardSuit.CLUB], 1),
            (CardType.CHITU, [5], [CardSuit.HEART, CardSuit.DIAMOND], 1),
            (CardType.DILU, [5], [CardSuit.SPADE, CardSuit.CLUB], 1),
        ]
        self.draw_pile = []
        self.card_id_counter = 0
        for card_type, numbers, suits_list, per_suit_count in cards_def:
            for num in numbers:
                for suit in suits_list:
                    count = per_suit_count if per_suit_count > 0 else 1
                    for _ in range(count):
                        if card_type in [CardType.SHA, CardType.SHAN, CardType.TAO]:
                            cat = CardCategory.BASIC
                        elif card_type in [CardType.ZHUGE, CardType.QINGLONG, CardType.ZHANGBA,
                                          CardType.BAGUA, CardType.CHITU, CardType.DILU]:
                            cat = CardCategory.EQUIPMENT
                        else:
                            cat = CardCategory.STRATEGY
                        self.card_id_counter += 1
                        self.draw_pile.append(Card(
                            id=self.card_id_counter, name=card_type.value,
                            card_type=card_type, suit=suit, number=num, category=cat
                        ))
        random.shuffle(self.draw_pile)

    def draw_card(self) -> Optional[Card]:
        if not self.draw_pile:
            if not self.discard_pile:
                return None
            self.draw_pile = self.discard_pile[:]
            self.discard_pile = []
            random.shuffle(self.draw_pile)
        return self.draw_pile.pop()

    def add_player(self, player_id: str, name: str, hero: Hero, is_ai: bool = False, ai_level: str = "medium"):
        if len(self.players) >= self.max_players:
            return False
        player = Player(player_id, name, hero, is_ai, ai_level)
        self.players.append(player)
        return True

    def remove_player(self, player_id: str):
        self.players = [p for p in self.players if p.player_id != player_id]

    def get_player(self, player_id: str) -> Optional[Player]:
        for p in self.players:
            if p.player_id == player_id:
                return p
        return None

    def get_alive_players(self) -> list[Player]:
        return [p for p in self.players if p.is_alive]

    def get_current_player(self) -> Optional[Player]:
        alive = self.get_alive_players()
        if not alive:
            return None
        if self.current_player_idx >= len(alive):
            self.current_player_idx = 0
        return alive[self.current_player_idx]

    def start_game(self):
        if len(self.get_alive_players()) < self.min_players:
            return False
        self.game_started = True
        for player in self.players:
            player.draw_cards(self, 4)
        self.current_player_idx = 0
        self.turn_count = 1
        self.phase = GamePhase.JUDGE
        self.add_log(f"游戏开始！共 {len(self.get_alive_players())} 名玩家")
        return True

    def next_turn(self):
        alive = self.get_alive_players()
        if len(alive) <= 1:
            self.phase = GamePhase.GAME_OVER
            winner = alive[0] if alive else None
            self.add_log(f"游戏结束！{'平局' if not winner else f'{winner.name} 获胜！'}")
            return
        self.current_player_idx = (self.current_player_idx + 1) % len(alive)
        self.turn_count += 1
        current = self.get_current_player()
        current.has_played_sha = False
        current.sha_count_limit = 1
        if current.equipment.get("weapon") and current.equipment["weapon"].card_type == CardType.ZHUGE:
            current.sha_count_limit = 999
        self.phase = GamePhase.JUDGE
        self.add_log(f"轮到 {current.name} 的回合")

    def add_log(self, msg: str):
        self.log.append(msg)
        if len(self.log) > 100:
            self.log = self.log[-100:]

    def to_dict(self, player_id: str = None):
        current = self.get_current_player()
        return {
            "room_id": self.room_id,
            "players": [p.to_dict(hide_hand=(p.player_id != player_id)) for p in self.players],
            "current_player_id": current.player_id if current else None,
            "phase": self.phase.value,
            "turn_count": self.turn_count,
            "game_started": self.game_started,
            "draw_pile_count": len(self.draw_pile),
            "discard_pile_count": len(self.discard_pile),
            "log": self.log[-20:],
            "pending_action": self.pending_action,
            "my_player": self.get_player(player_id).to_dict(hide_hand=False) if player_id and self.get_player(player_id) else None
        }


class AIPlayer:
    @staticmethod
    def decide_action(game: Game, player: Player) -> dict:
        level = player.ai_level
        if level == "easy":
            return AIPlayer._easy_ai(game, player)
        elif level == "medium":
            return AIPlayer._medium_ai(game, player)
        else:
            return AIPlayer._hard_ai(game, player)

    @staticmethod
    def _easy_ai(game: Game, player: Player) -> dict:
        alive = game.get_alive_players()
        enemies = [p for p in alive if p != player]
        if not enemies:
            return {"action": "end_play"}
        if player.hp < player.max_hp:
            for card in player.hand_cards:
                if card.card_type == CardType.TAO:
                    return {"action": "use_card", "card_id": card.id, "target": None}
        for card in player.hand_cards:
            if card.card_type == CardType.SHA and not player.has_played_sha:
                target = random.choice(enemies)
                return {"action": "use_card", "card_id": card.id, "target": target.player_id}
        for card in player.hand_cards:
            if card.card_type in [CardType.GUOHE, CardType.SHUNSHOU, CardType.JUEDOU]:
                target = random.choice(enemies)
                return {"action": "use_card", "card_id": card.id, "target": target.player_id}
            if card.card_type in [CardType.WUZHONG, CardType.TAOYUAN, CardType.WUGU]:
                return {"action": "use_card", "card_id": card.id, "target": None}
            if card.card_type in [CardType.NANMAN, CardType.WANJIAN]:
                return {"action": "use_card", "card_id": card.id, "target": None}
        for card in player.hand_cards:
            if card.category == CardCategory.EQUIPMENT:
                return {"action": "use_card", "card_id": card.id, "target": None}
        return {"action": "end_play"}

    @staticmethod
    def _medium_ai(game: Game, player: Player) -> dict:
        alive = game.get_alive_players()
        enemies = [p for p in alive if p != player]
        if not enemies:
            return {"action": "end_play"}
        enemies_by_hp = sorted(enemies, key=lambda p: p.hp)
        if player.hp <= 1:
            for card in player.hand_cards:
                if card.card_type == CardType.TAO:
                    return {"action": "use_card", "card_id": card.id, "target": None}
        for card in player.hand_cards:
            if card.card_type in [CardType.ZHUGE, CardType.QINGLONG, CardType.ZHANGBA]:
                if not player.equipment.get("weapon"):
                    return {"action": "use_card", "card_id": card.id, "target": None}
        for card in player.hand_cards:
            if card.card_type == CardType.SHA and not player.has_played_sha:
                target = enemies_by_hp[0]
                return {"action": "use_card", "card_id": card.id, "target": target.player_id}
        for card in player.hand_cards:
            if card.card_type in [CardType.GUOHE, CardType.SHUNSHOU]:
                target = enemies_by_hp[0]
                return {"action": "use_card", "card_id": card.id, "target": target.player_id}
        for card in player.hand_cards:
            if card.card_type == CardType.JUEDOU:
                target = enemies_by_hp[0]
                return {"action": "use_card", "card_id": card.id, "target": target.player_id}
        for card in player.hand_cards:
            if card.card_type == CardType.WUZHONG:
                return {"action": "use_card", "card_id": card.id, "target": None}
        for card in player.hand_cards:
            if card.card_type in [CardType.NANMAN, CardType.WANJIAN]:
                return {"action": "use_card", "card_id": card.id, "target": None}
        for card in player.hand_cards:
            if card.card_type in [CardType.BAGUA, CardType.DILU, CardType.CHITU]:
                slot = AIPlayer._get_equip_slot(card)
                if slot and not player.equipment.get(slot):
                    return {"action": "use_card", "card_id": card.id, "target": None}
        if player.hp < player.max_hp:
            for card in player.hand_cards:
                if card.card_type == CardType.TAOYUAN:
                    return {"action": "use_card", "card_id": card.id, "target": None}
        return {"action": "end_play"}

    @staticmethod
    def _hard_ai(game: Game, player: Player) -> dict:
        alive = game.get_alive_players()
        enemies = [p for p in alive if p != player]
        if not enemies:
            return {"action": "end_play"}
        sha_cards = [c for c in player.hand_cards if c.card_type == CardType.SHA]
        shan_cards = [c for c in player.hand_cards if c.card_type == CardType.SHAN]
        tao_cards = [c for c in player.hand_cards if c.card_type == CardType.TAO]
        def threat_score(p):
            return p.hp * 10 + len(p.hand_cards)
        enemies_by_threat = sorted(enemies, key=threat_score)
        if player.hp <= 2 and tao_cards:
            return {"action": "use_card", "card_id": tao_cards[0].id, "target": None}
        for card in player.hand_cards:
            if card.card_type in [CardType.ZHUGE, CardType.QINGLONG, CardType.ZHANGBA]:
                if not player.equipment.get("weapon"):
                    return {"action": "use_card", "card_id": card.id, "target": None}
            if card.card_type == CardType.BAGUA and not player.equipment.get("armor"):
                return {"action": "use_card", "card_id": card.id, "target": None}
            if card.card_type == CardType.DILU and not player.equipment.get("mount_plus"):
                return {"action": "use_card", "card_id": card.id, "target": None}
            if card.card_type == CardType.CHITU and not player.equipment.get("mount_minus"):
                return {"action": "use_card", "card_id": card.id, "target": None}
        for card in player.hand_cards:
            if card.card_type == CardType.GUOHE:
                target = enemies_by_threat[0]
                return {"action": "use_card", "card_id": card.id, "target": target.player_id}
        for card in player.hand_cards:
            if card.card_type == CardType.SHUNSHOU:
                target = enemies_by_threat[0]
                return {"action": "use_card", "card_id": card.id, "target": target.player_id}
        if len(sha_cards) >= 2:
            for card in player.hand_cards:
                if card.card_type == CardType.NANMAN:
                    return {"action": "use_card", "card_id": card.id, "target": None}
        if len(shan_cards) >= 2:
            for card in player.hand_cards:
                if card.card_type == CardType.WANJIAN:
                    return {"action": "use_card", "card_id": card.id, "target": None}
        for card in player.hand_cards:
            if card.card_type == CardType.JUEDOU:
                target = min(enemies, key=lambda p: len(p.hand_cards))
                return {"action": "use_card", "card_id": card.id, "target": target.player_id}
        if sha_cards and not player.has_played_sha:
            target = enemies_by_threat[0]
            return {"action": "use_card", "card_id": sha_cards[0].id, "target": target.player_id}
        for card in player.hand_cards:
            if card.card_type == CardType.WUZHONG:
                return {"action": "use_card", "card_id": card.id, "target": None}
        for card in player.hand_cards:
            if card.card_type == CardType.WUGU:
                return {"action": "use_card", "card_id": card.id, "target": None}
        if player.hp < player.max_hp:
            for card in player.hand_cards:
                if card.card_type == CardType.TAOYUAN:
                    return {"action": "use_card", "card_id": card.id, "target": None}
        return {"action": "end_play"}

    @staticmethod
    def _get_equip_slot(card: Card) -> Optional[str]:
        if card.card_type in [CardType.ZHUGE, CardType.QINGLONG, CardType.ZHANGBA]:
            return "weapon"
        if card.card_type == CardType.BAGUA:
            return "armor"
        if card.card_type == CardType.CHITU:
            return "mount_minus"
        if card.card_type == CardType.DILU:
            return "mount_plus"
        return None

    @staticmethod
    def decide_response(game: Game, player: Player, required_card: str) -> dict:
        level = player.ai_level
        if required_card == "shan":
            for card in player.hand_cards:
                if card.card_type == CardType.SHAN:
                    return {"action": "respond", "card_id": card.id}
            if player.equipment.get("armor") and player.equipment["armor"].card_type == CardType.BAGUA:
                return {"action": "use_bagua"}
            return {"action": "pass"}
        elif required_card == "sha":
            for card in player.hand_cards:
                if card.card_type == CardType.SHA:
                    return {"action": "respond", "card_id": card.id}
            return {"action": "pass"}
        elif required_card == "wuxie":
            if level == "easy":
                return {"action": "pass"}
            for card in player.hand_cards:
                if card.card_type == CardType.WUXIE:
                    return {"action": "respond", "card_id": card.id}
            return {"action": "pass"}
        return {"action": "pass"}


HEROES = [
    Hero("曹操", 4, "魏", skill_descriptions=["奸雄：受到伤害后，获得造成伤害的牌"]),
    Hero("司马懿", 3, "魏", skill_descriptions=["反馈：受到伤害后，获得伤害来源一张牌", "鬼才：可打出一张手牌代替判定牌"]),
    Hero("夏侯惇", 4, "魏", skill_descriptions=["刚烈：受到伤害后可判定，不为红桃则伤害来源选择弃两张牌或受到1点伤害"]),
    Hero("张辽", 4, "魏", skill_descriptions=["突袭：摸牌阶段可改为获得最多两名角色各一张手牌"]),
    Hero("许褚", 4, "魏", skill_descriptions=["裸衣：摸牌阶段可少摸一张牌，本回合杀和决斗伤害+1"]),
    Hero("郭嘉", 3, "魏", skill_descriptions=["天妒：判定牌生效后可获得之", "遗计：受到伤害后可摸两张牌"]),
    Hero("甄姬", 3, "魏", skill_descriptions=["洛神：判定为黑色则获得并继续判定", "倾国：黑色手牌可当闪"]),
    Hero("刘备", 4, "蜀", skill_descriptions=["仁德：可将任意张手牌交给其他角色"]),
    Hero("关羽", 4, "蜀", skill_descriptions=["武圣：红色牌可当杀"]),
    Hero("张飞", 4, "蜀", skill_descriptions=["咆哮：出牌阶段可使用任意张杀"]),
    Hero("诸葛亮", 3, "蜀", skill_descriptions=["观星：摸牌阶段可观看牌堆顶5张牌", "空城：无手牌时不受杀和决斗影响"]),
    Hero("赵云", 4, "蜀", skill_descriptions=["龙胆：杀当闪，闪当杀"]),
    Hero("马超", 4, "蜀", skill_descriptions=["马术：始终视为装备-1马", "铁骑：使用杀时可判定，红色则不可闪避"]),
    Hero("黄月英", 3, "蜀", skill_descriptions=["集智：使用非延时锦囊时摸一张牌", "奇才：锦囊无距离限制"]),
    Hero("孙权", 4, "吴", skill_descriptions=["制衡：出牌阶段可弃任意张牌摸等量牌"]),
    Hero("周瑜", 3, "吴", skill_descriptions=["英姿：摸牌阶段多摸一张牌", "反间：可令一名角色猜花色，猜错则受到1点伤害"]),
    Hero("黄盖", 4, "吴", skill_descriptions=["苦肉：可失去1点体力摸两张牌"]),
    Hero("吕蒙", 4, "吴", skill_descriptions=["克己：若出牌阶段未使用杀，则跳过弃牌阶段"]),
    Hero("陆逊", 3, "吴", skill_descriptions=["谦逊：不受顺手牵羊和乐不思蜀影响", "连营：失去最后一张手牌时摸一张牌"]),
    Hero("大乔", 3, "吴", skill_descriptions=["国色：方块牌可当乐不思蜀", "流离：成为杀的目标时可弃一张牌转移给攻击范围内另一名角色"]),
    Hero("甘宁", 4, "吴", skill_descriptions=["奇袭：黑色牌可当过河拆桥"]),
    Hero("吕布", 4, "群", skill_descriptions=["无双：使用杀需两张闪才能抵消，决斗需两张杀"]),
    Hero("貂蝉", 3, "群", skill_descriptions=["离间：弃一张牌令两名男性角色决斗", "闭月：结束阶段摸一张牌"]),
    Hero("华佗", 3, "群", skill_descriptions=["急救：可弃一张红色牌当桃", "青囊：出牌阶段可弃一张牌令一名角色回复1点体力"]),
    Hero("张角", 3, "群", skill_descriptions=["雷击：使用闪后可判定，黑桃则对一名角色造成2点伤害", "鬼道：可用黑色牌替换判定牌"]),
    Hero("袁绍", 4, "群", skill_descriptions=["乱击：两张同花色牌当万箭齐发"]),
    Hero("董卓", 8, "群", skill_descriptions=["酒池：可将黑桃手牌当酒", "肉林：女性角色对你使用的杀需两张闪"]),
]
