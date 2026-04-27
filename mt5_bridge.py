"""
DnR Bridge — Python CMD WebSocket Bridge for MT5
Port: 8765  (EA bridge: 8766)

INSTALL:
    pip install MetaTrader5 websockets asyncio

RUN:
    python dnr_bridge.py

Connect DnR Terminal to: ws://localhost:8765
"""

import asyncio
import json
import logging
import sys
from datetime import datetime

try:
    import MetaTrader5 as mt5
except ImportError:
    print("❌ MetaTrader5 not installed. Run: pip install MetaTrader5")
    sys.exit(1)

try:
    import websockets
except ImportError:
    print("❌ websockets not installed. Run: pip install websockets")
    sys.exit(1)

# ── Config ────────────────────────────────────────────────────────────
PORT         = 8765
TICK_INTERVAL = 1.0   # seconds between tick pushes
LOG_LEVEL    = logging.INFO

SYMBOLS = [
    "EURUSD","GBPUSD","USDJPY","AUDUSD","USDCHF","USDCAD","NZDUSD",
    "EURGBP","EURJPY","EURCAD","EURAUD","EURNZD","EURCHF",
    "GBPJPY","GBPAUD","GBPCAD","GBPCHF","GBPNZD",
    "AUDJPY","AUDCAD","AUDCHF","AUDNZD","CADJPY","CHFJPY","NZDJPY",
    "XAUUSD","XAGUSD","USOIL","UKOIL","USTEC","US30","US500",
    "BTCUSD","ETHUSD",
]

TF_MAP = {
    "M1":  mt5.TIMEFRAME_M1,  "M5":  mt5.TIMEFRAME_M5,
    "M15": mt5.TIMEFRAME_M15, "M30": mt5.TIMEFRAME_M30,
    "H1":  mt5.TIMEFRAME_H1,  "H4":  mt5.TIMEFRAME_H4,
    "D1":  mt5.TIMEFRAME_D1,  "W1":  mt5.TIMEFRAME_W1,
}

# ── Logging ───────────────────────────────────────────────────────────
logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("DnR-Bridge")

# ── MT5 Connection ────────────────────────────────────────────────────
def init_mt5():
    if not mt5.initialize():
        log.error(f"MT5 initialize failed: {mt5.last_error()}")
        return False
    info = mt5.account_info()
    if info is None:
        log.error("No MT5 account — open MetaTrader 5 first")
        return False
    log.info(f"✅ MT5 connected — {info.name} | {info.server} | Balance: {info.balance}")
    return True

def get_account_info():
    info = mt5.account_info()
    if info is None:
        return {}
    return {
        "balance":     round(info.balance, 2),
        "equity":      round(info.equity, 2),
        "margin":      round(info.margin, 2),
        "free_margin": round(info.margin_free, 2),
        "profit":      round(info.profit, 2),
        "leverage":    info.leverage,
        "currency":    info.currency,
        "name":        info.name,
        "server":      info.server,
    }

def get_positions():
    positions = mt5.positions_get()
    if positions is None:
        return []
    result = []
    for p in positions:
        sym   = p.symbol
        digs  = mt5.symbol_info(sym).digits if mt5.symbol_info(sym) else 5
        result.append({
            "ticket":        p.ticket,
            "symbol":        sym,
            "type":          "BUY" if p.type == mt5.ORDER_TYPE_BUY else "SELL",
            "volume":        p.volume,
            "price_open":    round(p.price_open,    digs),
            "price_current": round(p.price_current, digs),
            "sl":            round(p.sl, digs),
            "tp":            round(p.tp, digs),
            "profit":        round(p.profit, 2),
            "magic":         p.magic,
            "comment":       p.comment,
        })
    return result

def get_ticks():
    data = {}
    for sym in SYMBOLS:
        info = mt5.symbol_info(sym)
        if info is None or not info.visible:
            continue
        tick = mt5.symbol_info_tick(sym)
        if tick is None:
            continue
        digs = info.digits
        pt   = info.point
        spr  = round((tick.ask - tick.bid) / pt / 10, 1) if pt > 0 else 0
        data[sym] = {
            "bid":    round(tick.bid, digs),
            "ask":    round(tick.ask, digs),
            "spread": spr,
            "digits": digs,
            "time":   datetime.fromtimestamp(tick.time).isoformat(),
        }
    return data

def get_candles(symbol, timeframe_str, count=100):
    tf = TF_MAP.get(timeframe_str, mt5.TIMEFRAME_H1)
    rates = mt5.copy_rates_from_pos(symbol, tf, 0, count)
    if rates is None:
        return []
    info = mt5.symbol_info(symbol)
    digs = info.digits if info else 5
    result = []
    for r in rates:
        result.append({
            "time":   datetime.fromtimestamp(r["time"]).isoformat(),
            "open":   round(float(r["open"]),  digs),
            "high":   round(float(r["high"]),  digs),
            "low":    round(float(r["low"]),   digs),
            "close":  round(float(r["close"]), digs),
            "volume": int(r["tick_volume"]),
        })
    return result

def execute_order(symbol, action, volume, sl, tp):
    info = mt5.symbol_info(symbol)
    if info is None:
        return {"success": False, "error": f"Symbol {symbol} not found"}

    digs  = info.digits
    tick  = mt5.symbol_info_tick(symbol)
    price = tick.ask if action == "BUY" else tick.bid
    otype = mt5.ORDER_TYPE_BUY if action == "BUY" else mt5.ORDER_TYPE_SELL

    request = {
        "action":                mt5.TRADE_ACTION_DEAL,
        "symbol":                symbol,
        "volume":                float(volume),
        "type":                  otype,
        "price":                 round(price, digs),
        "sl":                    round(float(sl), digs) if sl else 0.0,
        "tp":                    round(float(tp), digs) if tp else 0.0,
        "deviation":             30,
        "magic":                 20260101,
        "comment":               "DnR Bridge",
        "type_time":             mt5.ORDER_TIME_GTC,
        "type_filling":          mt5.ORDER_FILLING_IOC,
    }

    result = mt5.order_send(request)
    if result is None:
        err = mt5.last_error()
        return {"success": False, "error": f"order_send failed: {err}"}

    if result.retcode == mt5.TRADE_RETCODE_DONE:
        return {
            "success": True,
            "ticket":  result.order,
            "price":   round(result.price, digs),
            "action":  action,
            "symbol":  symbol,
        }
    else:
        return {
            "success": False,
            "error":   f"Order ditolak: {result.comment} (retcode {result.retcode})",
            "retcode": result.retcode,
        }

def close_position(ticket):
    pos = mt5.positions_get(ticket=ticket)
    if not pos:
        return {"success": False, "ticket": ticket, "error": "Position not found"}

    p     = pos[0]
    sym   = p.symbol
    digs  = mt5.symbol_info(sym).digits if mt5.symbol_info(sym) else 5
    tick  = mt5.symbol_info_tick(sym)
    price = tick.bid if p.type == mt5.ORDER_TYPE_BUY else tick.ask
    otype = mt5.ORDER_TYPE_SELL if p.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY

    request = {
        "action":       mt5.TRADE_ACTION_DEAL,
        "symbol":       sym,
        "volume":       p.volume,
        "type":         otype,
        "position":     ticket,
        "price":        round(price, digs),
        "deviation":    30,
        "magic":        20260101,
        "comment":      "DnR Close",
        "type_time":    mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }

    result = mt5.order_send(request)
    if result and result.retcode == mt5.TRADE_RETCODE_DONE:
        return {"success": True, "ticket": ticket}
    else:
        err = result.comment if result else str(mt5.last_error())
        return {"success": False, "ticket": ticket, "error": err}

# ── WebSocket Handler ─────────────────────────────────────────────────
connected_clients = set()

async def handler(websocket):
    addr = websocket.remote_address
    log.info(f"Client connected: {addr}")
    connected_clients.add(websocket)

    try:
        # Send initial account + positions
        await websocket.send(json.dumps({
            "type":      "account",
            "account":   get_account_info(),
            "positions": get_positions(),
        }))

        async for raw in websocket:
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            mtype = msg.get("type", "")

            if mtype == "get_candles":
                sym   = msg.get("symbol", "EURUSD")
                tf    = msg.get("timeframe", "H1")
                count = int(msg.get("count", 100))
                candles = get_candles(sym, tf, count)
                await websocket.send(json.dumps({
                    "type":   "candles",
                    "symbol": sym,
                    "data":   candles,
                }))

            elif mtype == "get_positions":
                await websocket.send(json.dumps({
                    "type":      "account",
                    "account":   get_account_info(),
                    "positions": get_positions(),
                }))

            elif mtype == "send_order":
                r = execute_order(
                    msg.get("symbol"), msg.get("action"),
                    msg.get("volume", 0.01),
                    msg.get("sl", 0), msg.get("tp", 0),
                )
                await websocket.send(json.dumps({"type": "order_result", **r}))
                if r.get("success"):
                    await websocket.send(json.dumps({
                        "type": "account",
                        "account": get_account_info(),
                        "positions": get_positions(),
                    }))

            elif mtype == "close_position":
                r = close_position(int(msg.get("ticket", 0)))
                await websocket.send(json.dumps({"type": "close_result", **r}))
                if r.get("success"):
                    await websocket.send(json.dumps({
                        "type": "account",
                        "account": get_account_info(),
                        "positions": get_positions(),
                    }))

            elif mtype == "ping":
                await websocket.send(json.dumps({"type": "pong"}))

    except websockets.exceptions.ConnectionClosed:
        pass
    finally:
        connected_clients.discard(websocket)
        log.info(f"Client disconnected: {addr}")

# ── Tick broadcast loop ───────────────────────────────────────────────
async def tick_loop():
    while True:
        await asyncio.sleep(TICK_INTERVAL)
        if not connected_clients:
            continue
        try:
            data = get_ticks()
            if not data:
                continue
            payload = json.dumps({"type": "ticks", "data": data})
            dead = set()
            for ws in connected_clients.copy():
                try:
                    await ws.send(payload)
                except Exception:
                    dead.add(ws)
            connected_clients -= dead
        except Exception as e:
            log.warning(f"Tick loop error: {e}")

# ── Main ──────────────────────────────────────────────────────────────
async def main():
    if not init_mt5():
        sys.exit(1)

    async with websockets.serve(handler, "localhost", PORT):
        log.info(f"✅ DnR Python Bridge running on ws://localhost:{PORT}")
        log.info("   Connect DnR Terminal → CMD mode → ws://localhost:8765")
        log.info("   Press Ctrl+C to stop")
        tick_task = asyncio.create_task(tick_loop())
        try:
            await asyncio.Future()  # run forever
        except asyncio.CancelledError:
            tick_task.cancel()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Bridge stopped.")
        mt5.shutdown()
