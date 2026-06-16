
"""
三国杀 - Flask + WebSocket 服务器
"""
import json
import uuid
import random
import time
import threading
import os
import hashlib
import urllib.request
import urllib.error
from flask import Flask, render_template, request, jsonify, session, send_from_directory, Response
from flask_socketio import SocketIO, emit, join_room, leave_room
from game_engine import Game, Hero, HEROES, AIPlayer, CardType, CardCategory, GamePhase

app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'sanguosha-dev-secret-change-me')
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

rooms: dict[str, Game] = {}
player_rooms: dict[str, str] = {}
ai_threads: dict[str, threading.Event] = {}
ROOM_TTL_SECONDS = int(os.getenv("ROOM_TTL_SECONDS", "1800"))
cleanup_started = False


def generate_room_id():
    return ''.join(random.choices('0123456789', k=6))


def now_ts():
    return time.time()


def get_client_fingerprint() -> str:
    forwarded = request.headers.get('X-Forwarded-For', '')
    ip = (forwarded.split(',')[0].strip() if forwarded else request.remote_addr) or 'unknown-ip'
    ua = request.headers.get('User-Agent', 'unknown-ua')
    return hashlib.md5(f'{ua}|{ip}'.encode('utf-8')).hexdigest()


def default_player_name() -> str:
    return '玩家' + get_client_fingerprint()[:6]


def touch_room(game: Game):
    game.last_active_at = now_ts()


def cleanup_expired_rooms():
    expired = []
    now = now_ts()
    for room_id, game in list(rooms.items()):
        created_at = getattr(game, 'created_at', now)
        if now - created_at >= ROOM_TTL_SECONDS:
            expired.append(room_id)
    for room_id in expired:
        stop_ai_loop(room_id)
        rooms.pop(room_id, None)
        for sid, rid in list(player_rooms.items()):
            if rid == room_id:
                player_rooms.pop(sid, None)
        socketio.emit('room_closed', {'room_id': room_id, 'message': '房间已超过30分钟，自动解散'})
    if expired:
        broadcast_room_list()


def cleanup_loop():
    while True:
        cleanup_expired_rooms()
        time.sleep(30)


def ensure_cleanup_loop():
    global cleanup_started
    if not cleanup_started:
        cleanup_started = True
        socketio.start_background_task(cleanup_loop)


def find_player_by_fingerprint(game: Game, fingerprint: str):
    for player in game.players:
        if getattr(player, 'fingerprint', None) == fingerprint:
            return player
    return None


def bind_player_session(game: Game, player, room_id: str):
    old_sid = player.player_id
    if old_sid != request.sid:
        player.player_id = request.sid
        player_rooms.pop(old_sid, None)
    player.fingerprint = get_client_fingerprint()
    player.is_ai = False
    if player.name.endswith('(AI)'):
        player.name = player.name[:-4]
    player_rooms[request.sid] = room_id
    touch_room(game)


def advance_to_play_phase(room_id: str):
    if room_id not in rooms:
        return
    game = rooms[room_id]
    current = game.get_current_player()
    if not current or game.phase == GamePhase.GAME_OVER:
        return
    touch_room(game)
    game.pending_action = None
    game.phase = GamePhase.JUDGE
    game.add_log(f'{current.name} 的判定阶段')
    broadcast_game_state(room_id)
    time.sleep(0.25)
    game.phase = GamePhase.DRAW
    if not current.skip_draw:
        current.draw_cards(game, 2)
        game.add_log(f'{current.name} 摸了2张牌')
    current.skip_draw = False
    broadcast_game_state(room_id)
    time.sleep(0.25)
    if current.skip_play:
        current.skip_play = False
        game.phase = GamePhase.DISCARD
        _handle_discard_phase(game, current)
        return
    game.phase = GamePhase.PLAY
    broadcast_game_state(room_id)
    if current.is_ai:
        socketio.start_background_task(run_ai_play_phase, room_id)


def schedule_current_turn(room_id: str):
    if room_id in rooms and rooms[room_id].phase != GamePhase.GAME_OVER:
        socketio.start_background_task(advance_to_play_phase, room_id)




HERMES_DIST = os.getenv('HERMES_DIST', '/root/.openclaw/workspace/hermes_agent/frontend/dist')
HERMES_BACKEND = os.getenv('HERMES_BACKEND', 'http://127.0.0.1:5000')


@app.route('/hermes/')
def hermes_index():
    return send_from_directory(HERMES_DIST, 'index.html')


@app.route('/hermes/<path:path>')
def hermes_static(path):
    target = os.path.join(HERMES_DIST, path)
    if os.path.exists(target) and os.path.isfile(target):
        return send_from_directory(HERMES_DIST, path)
    return send_from_directory(HERMES_DIST, 'index.html')


@app.route('/hermes/api/<path:path>', methods=['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS'])
def hermes_api_proxy(path):
    query = request.query_string.decode('utf-8')
    url = f"{HERMES_BACKEND}/api/{path}" + (f"?{query}" if query else '')
    headers = {k: v for k, v in request.headers.items() if k.lower() not in {'host', 'content-length', 'connection', 'accept-encoding'}}
    data = request.get_data() if request.method not in {'GET', 'HEAD'} else None
    req = urllib.request.Request(url, data=data, headers=headers, method=request.method)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = resp.read()
            excluded = {'transfer-encoding', 'connection', 'content-encoding'}
            response_headers = [(k, v) for k, v in resp.headers.items() if k.lower() not in excluded]
            return Response(body, status=resp.status, headers=response_headers)
    except urllib.error.HTTPError as e:
        body = e.read()
        return Response(body, status=e.code, content_type=e.headers.get('Content-Type', 'application/json'))
    except Exception as e:
        return jsonify({'error': 'Hermes backend unavailable', 'detail': str(e)}), 502


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/heroes')
def get_heroes():
    return jsonify([h.to_dict() for h in HEROES])


@app.route('/api/rooms')
def get_rooms():
    cleanup_expired_rooms()
    room_list = []
    for rid, game in rooms.items():
        human_count = sum(1 for p in game.players if not p.is_ai)
        room_list.append({
            "room_id": rid,
            "player_count": len(game.players),
            "human_count": human_count,
            "max_players": game.max_players,
            "game_started": game.game_started,
            "players": [{"name": p.name, "hero": p.hero.name, "is_ai": p.is_ai} for p in game.players]
        })
    return jsonify(room_list)


@socketio.on('connect')
def handle_connect():
    ensure_cleanup_loop()
    print(f"Client connected: {request.sid}")


@socketio.on('disconnect')
def handle_disconnect():
    print(f"Client disconnected: {request.sid}")
    if request.sid in player_rooms:
        room_id = player_rooms.pop(request.sid)
        if room_id in rooms:
            game = rooms[room_id]
            player = game.get_player(request.sid)
            if player and not player.is_ai:
                player.is_ai = True
                player.ai_level = "medium"
                if not player.name.endswith('(AI)'):
                    player.name = f"{player.name}(AI)"
                game.add_log(f"{player.name} 断线，可重进；暂由AI接管")
                broadcast_game_state(room_id)


@socketio.on('create_room')
def handle_create_room(data):
    cleanup_expired_rooms()
    player_name = (data.get('name') or '').strip() or default_player_name()
    hero_name = data.get('hero', HEROES[0].name)
    room_id = generate_room_id()
    while room_id in rooms:
        room_id = generate_room_id()
    game = Game(room_id)
    hero = next((h for h in HEROES if h.name == hero_name), HEROES[0])
    game.add_player(request.sid, player_name, hero, is_ai=False)
    player = game.get_player(request.sid)
    bind_player_session(game, player, room_id)
    rooms[room_id] = game
    join_room(room_id)
    emit('room_created', {
        'room_id': room_id,
        'player_id': request.sid,
        'game': game.to_dict(request.sid)
    })
    broadcast_room_list()


@socketio.on('join_room')
def handle_join_room(data):
    cleanup_expired_rooms()
    room_id = data.get('room_id')
    player_name = (data.get('name') or '').strip() or default_player_name()
    hero_name = data.get('hero', HEROES[0].name)
    if room_id not in rooms:
        emit('error', {'message': '房间不存在'})
        return
    game = rooms[room_id]
    fingerprint = get_client_fingerprint()
    existing = find_player_by_fingerprint(game, fingerprint)
    if existing:
        bind_player_session(game, existing, room_id)
        join_room(room_id)
        game.add_log(f"{existing.name} 已重连")
        emit('room_joined', {
            'room_id': room_id,
            'player_id': request.sid,
            'game': game.to_dict(request.sid)
        })
        broadcast_game_state(room_id)
        broadcast_room_list()
        return
    if game.game_started:
        emit('error', {'message': '游戏已开始；只有断线玩家可重进'})
        return
    hero = next((h for h in HEROES if h.name == hero_name), HEROES[0])
    if not game.add_player(request.sid, player_name, hero, is_ai=False):
        emit('error', {'message': '房间已满'})
        return
    player = game.get_player(request.sid)
    bind_player_session(game, player, room_id)
    join_room(room_id)
    emit('room_joined', {
        'room_id': room_id,
        'player_id': request.sid,
        'game': game.to_dict(request.sid)
    })
    broadcast_game_state(room_id)
    broadcast_room_list()


@socketio.on('add_ai')
def handle_add_ai(data):
    room_id = data.get('room_id')
    if room_id not in rooms:
        return
    game = rooms[room_id]
    if game.game_started:
        return
    ai_level = data.get('ai_level', 'medium')
    ai_count = data.get('count', 1)
    ai_names = ["关羽", "张飞", "赵云", "马超", "黄忠", "吕布", "曹操", "孙权", "周瑜", "诸葛亮"]
    for i in range(ai_count):
        if len(game.players) >= game.max_players:
            break
        ai_name = random.choice(ai_names)
        hero = random.choice(HEROES)
        ai_id = f"ai_{uuid.uuid4().hex[:8]}"
        game.add_player(ai_id, f"{ai_name}(AI)", hero, is_ai=True, ai_level=ai_level)
    broadcast_game_state(room_id)
    broadcast_room_list()


@socketio.on('start_game')
def handle_start_game(data):
    room_id = data.get('room_id')
    if room_id not in rooms:
        return
    game = rooms[room_id]
    human_count = sum(1 for p in game.players if not p.is_ai)
    if human_count < 1:
        emit('error', {'message': '至少需要1名玩家'})
        return
    if len(game.players) < game.min_players:
        ai_names = ["关羽", "张飞", "赵云", "马超", "黄忠", "吕布", "曹操", "孙权", "周瑜", "诸葛亮"]
        for i in range(game.min_players - len(game.players)):
            ai_name = random.choice(ai_names)
            hero = random.choice(HEROES)
            ai_id = f"ai_{uuid.uuid4().hex[:8]}"
            game.add_player(ai_id, f"{ai_name}(AI)", hero, is_ai=True, ai_level="medium")
    if game.start_game():
        touch_room(game)
        broadcast_game_state(room_id)
        broadcast_room_list()
        start_ai_loop(room_id)
        schedule_current_turn(room_id)


@socketio.on('game_action')
def handle_game_action(data):
    room_id = data.get('room_id')
    action = data.get('action')
    if room_id not in rooms:
        return
    game = rooms[room_id]
    player = game.get_player(request.sid)
    if not player or player.is_ai:
        return
    result = process_player_action(game, player, action)
    if result:
        emit('action_result', result)
    broadcast_game_state(room_id)
    if game.phase == GamePhase.GAME_OVER:
        broadcast_game_state(room_id)
        stop_ai_loop(room_id)
        return
    current = game.get_current_player()
    if current and current.is_ai:
        schedule_current_turn(room_id)


def process_player_action(game: Game, player, action: dict) -> dict:
    action_type = action.get('type')
    if action_type == 'end_play':
        game.phase = GamePhase.DISCARD
        _handle_discard_phase(game, player)
        return {"success": True, "message": "结束出牌"}
    elif action_type == 'use_card':
        card_id = action.get('card_id')
        target_id = action.get('target_id')
        card = next((c for c in player.hand_cards if c.id == card_id), None)
        if not card:
            return {"success": False, "message": "卡牌不存在"}
        target = game.get_player(target_id) if target_id else None
        result = _execute_card(game, player, card, target)
        return result
    elif action_type == 'respond':
        card_id = action.get('card_id')
        card = next((c for c in player.hand_cards if c.id == card_id), None)
        if card:
            player.hand_cards.remove(card)
            game.discard_pile.append(card)
            game.pending_action = None
            return {"success": True, "message": f"{player.name} 使用了 {card.name}"}
        return {"success": False, "message": "卡牌不存在"}
    elif action_type == 'pass':
        game.pending_action = None
        return {"success": True, "message": f"{player.name} 放弃响应"}
    elif action_type == 'end_discard':
        game.next_turn()
        broadcast_game_state(game.room_id)
        schedule_current_turn(game.room_id)
        return {"success": True, "message": "回合结束"}
    return {"success": False, "message": "未知操作"}


def _execute_card(game: Game, player, card, target=None) -> dict:
    if card in player.hand_cards:
        player.hand_cards.remove(card)
    card_type = card.card_type
    if card_type == CardType.SHA:
        if player.has_played_sha and player.sha_count_limit <= 1:
            player.hand_cards.append(card)
            return {"success": False, "message": "本回合已使用过杀"}
        if not target:
            player.hand_cards.append(card)
            return {"success": False, "message": "请选择目标"}
        player.has_played_sha = True
        game.discard_pile.append(card)
        game.add_log(f"{player.name} 对 {target.name} 使用了【杀】")
        if target.is_ai:
            response = AIPlayer.decide_response(game, target, "shan")
            if response['action'] == 'respond':
                shan_card = next((c for c in target.hand_cards if c.id == response['card_id']), None)
                if shan_card:
                    target.hand_cards.remove(shan_card)
                    game.discard_pile.append(shan_card)
                    game.add_log(f"{target.name} 使用了【闪】")
                else:
                    target.lose_hp(game, 1, player)
                    game.add_log(f"{target.name} 受到1点伤害")
            else:
                target.lose_hp(game, 1, player)
                game.add_log(f"{target.name} 受到1点伤害")
        else:
            game.pending_action = {
                "type": "respond_shan",
                "source_id": player.player_id,
                "target_id": target.player_id,
                "card_name": "杀"
            }
        return {"success": True, "message": f"对 {target.name} 使用了杀"}
    elif card_type == CardType.SHAN:
        player.hand_cards.append(card)
        return {"success": False, "message": "闪不能主动使用"}
    elif card_type == CardType.TAO:
        if player.hp >= player.max_hp:
            player.hand_cards.append(card)
            return {"success": False, "message": "体力值已满"}
        player.recover_hp(1)
        game.discard_pile.append(card)
        game.add_log(f"{player.name} 使用了【桃】，回复1点体力")
        return {"success": True, "message": "回复1点体力"}
    elif card_type == CardType.GUOHE:
        if not target:
            player.hand_cards.append(card)
            return {"success": False, "message": "请选择目标"}
        game.discard_pile.append(card)
        game.add_log(f"{player.name} 对 {target.name} 使用了【过河拆桥】")
        if target.is_ai:
            if target.hand_cards:
                discarded = random.choice(target.hand_cards)
                target.hand_cards.remove(discarded)
                game.discard_pile.append(discarded)
                game.add_log(f"{target.name} 弃置了 {discarded.name}")
        else:
            game.pending_action = {
                "type": "discard_card",
                "target_id": target.player_id,
                "source_id": player.player_id,
                "card_name": "过河拆桥"
            }
        return {"success": True, "message": f"对 {target.name} 使用了过河拆桥"}
    elif card_type == CardType.SHUNSHOU:
        if not target:
            player.hand_cards.append(card)
            return {"success": False, "message": "请选择目标"}
        game.discard_pile.append(card)
        game.add_log(f"{player.name} 对 {target.name} 使用了【顺手牵羊】")
        if target.is_ai:
            if target.hand_cards:
                stolen = random.choice(target.hand_cards)
                target.hand_cards.remove(stolen)
                player.hand_cards.append(stolen)
                game.add_log(f"{player.name} 获得了 {target.name} 的 {stolen.name}")
        else:
            game.pending_action = {
                "type": "shunshou_card",
                "target_id": target.player_id,
                "source_id": player.player_id,
                "card_name": "顺手牵羊"
            }
        return {"success": True, "message": f"对 {target.name} 使用了顺手牵羊"}
    elif card_type == CardType.WUZHONG:
        game.discard_pile.append(card)
        player.draw_cards(game, 2)
        game.add_log(f"{player.name} 使用了【无中生有】，摸2张牌")
        return {"success": True, "message": "摸2张牌"}
    elif card_type == CardType.JUEDOU:
        if not target:
            player.hand_cards.append(card)
            return {"success": False, "message": "请选择目标"}
        game.discard_pile.append(card)
        game.add_log(f"{player.name} 对 {target.name} 使用了【决斗】")
        _resolve_juedou(game, player, target)
        return {"success": True, "message": f"与 {target.name} 决斗"}
    elif card_type == CardType.NANMAN:
        game.discard_pile.append(card)
        game.add_log(f"{player.name} 使用了【南蛮入侵】")
        _resolve_aoe(game, player, "sha")
        return {"success": True, "message": "使用了南蛮入侵"}
    elif card_type == CardType.WANJIAN:
        game.discard_pile.append(card)
        game.add_log(f"{player.name} 使用了【万箭齐发】")
        _resolve_aoe(game, player, "shan")
        return {"success": True, "message": "使用了万箭齐发"}
    elif card_type == CardType.TAOYUAN:
        game.discard_pile.append(card)
        game.add_log(f"{player.name} 使用了【桃园结义】")
        for p in game.get_alive_players():
            p.recover_hp(1)
        return {"success": True, "message": "所有角色回复1点体力"}
    elif card_type == CardType.WUGU:
        game.discard_pile.append(card)
        game.add_log(f"{player.name} 使用了【五谷丰登】")
        alive = game.get_alive_players()
        for p in alive:
            p.draw_cards(game, 1)
        return {"success": True, "message": "每人摸1张牌"}
    elif card.category == CardCategory.EQUIPMENT:
        slot = None
        if card_type in [CardType.ZHUGE, CardType.QINGLONG, CardType.ZHANGBA]:
            slot = "weapon"
        elif card_type == CardType.BAGUA:
            slot = "armor"
        elif card_type == CardType.CHITU:
            slot = "mount_minus"
        elif card_type == CardType.DILU:
            slot = "mount_plus"
        if slot:
            old = player.equipment.get(slot)
            if old:
                game.discard_pile.append(old)
            player.equipment[slot] = card
            game.add_log(f"{player.name} 装备了【{card.name}】")
            return {"success": True, "message": f"装备了 {card.name}"}
        return {"success": False, "message": "无法装备"}
    elif card_type in [CardType.BINGLIANG, CardType.LE_BU_SI_SHU, CardType.SHAN_DIAN]:
        if not target:
            player.hand_cards.append(card)
            return {"success": False, "message": "请选择目标"}
        target.judge_cards.append(card)
        game.add_log(f"{player.name} 对 {target.name} 使用了【{card.name}】")
        return {"success": True, "message": f"对 {target.name} 使用了 {card.name}"}
    return {"success": False, "message": "未知卡牌"}


def _resolve_juedou(game: Game, source, target):
    if target.is_ai:
        response = AIPlayer.decide_response(game, target, "sha")
        if response['action'] == 'respond':
            sha_card = next((c for c in target.hand_cards if c.id == response['card_id']), None)
            if sha_card:
                target.hand_cards.remove(sha_card)
                game.discard_pile.append(sha_card)
                game.add_log(f"{target.name} 出了【杀】")
                if source.is_ai:
                    response2 = AIPlayer.decide_response(game, source, "sha")
                    if response2['action'] != 'respond':
                        source.lose_hp(game, 1, target)
                        game.add_log(f"{source.name} 受到1点伤害")
                    else:
                        game.add_log(f"{source.name} 也出了【杀】，决斗平手")
                else:
                    game.pending_action = {
                        "type": "respond_juedou",
                        "source_id": target.player_id,
                        "target_id": source.player_id,
                    }
            else:
                target.lose_hp(game, 1, source)
                game.add_log(f"{target.name} 受到1点伤害")
        else:
            target.lose_hp(game, 1, source)
            game.add_log(f"{target.name} 受到1点伤害")
    else:
        game.pending_action = {
            "type": "respond_juedou",
            "source_id": source.player_id,
            "target_id": target.player_id,
        }


def _resolve_aoe(game: Game, source, required: str):
    alive = game.get_alive_players()
    for p in alive:
        if p == source:
            continue
        if p.is_ai:
            response = AIPlayer.decide_response(game, p, required)
            if response['action'] != 'respond':
                p.lose_hp(game, 1, source)
                game.add_log(f"{p.name} 受到1点伤害")
            else:
                card = next((c for c in p.hand_cards if c.id == response['card_id']), None)
                if card:
                    p.hand_cards.remove(card)
                    game.discard_pile.append(card)
                    game.add_log(f"{p.name} 出了【{'杀' if required == 'sha' else '闪'}】")


def _handle_discard_phase(game: Game, player):
    max_hand = player.hp
    if len(player.hand_cards) <= max_hand:
        game.next_turn()
        broadcast_game_state(game.room_id)
        schedule_current_turn(game.room_id)
    else:
        if player.is_ai:
            to_discard = len(player.hand_cards) - max_hand
            for _ in range(to_discard):
                if player.hand_cards:
                    equip = [c for c in player.hand_cards if c.category == CardCategory.EQUIPMENT]
                    if equip:
                        discarded = equip[0]
                    else:
                        discarded = player.hand_cards[0]
                    player.hand_cards.remove(discarded)
                    game.discard_pile.append(discarded)
            game.add_log(f"{player.name} 弃置了 {to_discard} 张牌")
            game.next_turn()
            broadcast_game_state(game.room_id)
            schedule_current_turn(game.room_id)
        else:
            game.pending_action = {
                "type": "discard_phase",
                "player_id": player.player_id,
                "max_hand": max_hand,
                "current_hand": len(player.hand_cards),
                "to_discard": len(player.hand_cards) - max_hand
            }


@socketio.on('discard_cards')
def handle_discard_cards(data):
    room_id = data.get('room_id')
    card_ids = data.get('card_ids', [])
    if room_id not in rooms:
        return
    game = rooms[room_id]
    player = game.get_player(request.sid)
    if not player:
        return
    for cid in card_ids:
        card = next((c for c in player.hand_cards if c.id == cid), None)
        if card:
            player.hand_cards.remove(card)
            game.discard_pile.append(card)
    game.add_log(f"{player.name} 弃置了 {len(card_ids)} 张牌")
    game.pending_action = None
    game.next_turn()
    broadcast_game_state(room_id)
    schedule_current_turn(room_id)


def run_ai_play_phase(room_id: str):
    if room_id not in rooms:
        return
    game = rooms[room_id]
    current = game.get_current_player()
    if not current or not current.is_ai:
        return
    if game.phase != GamePhase.PLAY:
        return
    max_actions = 20
    actions_taken = 0
    while actions_taken < max_actions:
        action = AIPlayer.decide_action(game, current)
        if action['action'] == 'end_play':
            break
        if action['action'] == 'use_card':
            card_id = action['card_id']
            target_id = action.get('target')
            card = next((c for c in current.hand_cards if c.id == card_id), None)
            target = game.get_player(target_id) if target_id else None
            if card:
                _execute_card(game, current, card, target)
                actions_taken += 1
                time.sleep(0.3)
                broadcast_game_state(room_id)
                if game.phase == GamePhase.GAME_OVER:
                    return
        else:
            break
    game.phase = GamePhase.DISCARD
    _handle_discard_phase(game, current)
    time.sleep(0.3)
    broadcast_game_state(room_id)


def start_ai_loop(room_id: str):
    if room_id in ai_threads:
        return
    stop_event = threading.Event()
    ai_threads[room_id] = stop_event
    def ai_loop():
        while not stop_event.is_set():
            if room_id not in rooms:
                break
            game = rooms[room_id]
            if game.phase == GamePhase.GAME_OVER:
                break
            current = game.get_current_player()
            if current and current.is_ai and game.phase == GamePhase.PLAY:
                run_ai_play_phase(room_id)
            time.sleep(1)
    thread = threading.Thread(target=ai_loop, daemon=True)
    thread.start()


def stop_ai_loop(room_id: str):
    if room_id in ai_threads:
        ai_threads[room_id].set()
        del ai_threads[room_id]


def broadcast_game_state(room_id: str):
    if room_id not in rooms:
        return
    game = rooms[room_id]
    # Public state first, then per-player private state. This prevents the
    # hidden-hand public payload from overwriting the current player's hand UI.
    socketio.emit('game_state', game.to_dict(), room=room_id)
    for player in game.players:
        if not player.is_ai:
            state = game.to_dict(player.player_id)
            socketio.emit('game_state', state, room=player.player_id)


def broadcast_room_list():
    room_list = []
    for rid, game in rooms.items():
        human_count = sum(1 for p in game.players if not p.is_ai)
        room_list.append({
            "room_id": rid,
            "player_count": len(game.players),
            "human_count": human_count,
            "max_players": game.max_players,
            "game_started": game.game_started,
            "players": [{"name": p.name, "hero": p.hero.name, "is_ai": p.is_ai} for p in game.players]
        })
    socketio.emit('room_list', room_list)


if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=int(os.getenv('PORT', '8000')), debug=os.getenv('FLASK_DEBUG', '0') == '1', allow_unsafe_werkzeug=True)
