import pandas as pd
import time
import requests
from binance.client import Client
from binance.exceptions import BinanceAPIException
import logging
import os
from dotenv import load_dotenv

# Carregar as variáveis do arquivo .env
load_dotenv()

# Carregar configurações das variáveis de ambiente
API_KEY = os.getenv("API_KEY")
API_SECRET = os.getenv("API_SECRET")
SYMBOL = "PEPEUSDT"  # Ajuste conforme necessário
SHORT_WINDOW = 8
LONG_WINDOW = 20
INTERVAL = "15m"
RISK_PERCENTAGE = 2
STOP_LOSS_PERCENTAGE = 2
TAKE_PROFIT_PERCENTAGE = 3
TRADE_ALLOCATION = 0.22859415

# Configuração do logger para armazenar logs
logging.basicConfig(
    filename="trading_log.txt",
    level=logging.INFO,
    format="%(asctime)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)

# Inicializa o cliente da Binance com as credenciais fornecidas
client = Client(API_KEY, API_SECRET)

def fetch_data(symbol, interval, limit=500):
    klines = client.get_klines(symbol=symbol, interval=interval, limit=limit)
    df = pd.DataFrame(klines, columns=["time", "open", "high", "low", "close", "volume", "close_time", "quote_asset_volume", "trades", "taker_buy_base", "taker_buy_quote", "ignore"])
    df["close"] = pd.to_numeric(df["close"])
    return df[["time", "close"]]

def calculate_moving_averages(data, short_window, long_window):
    data[f"SMA_{short_window}"] = data["close"].rolling(window=short_window).mean()
    data[f"SMA_{long_window}"] = data["close"].rolling(window=long_window).mean()
    return data

def make_decision(data):
    if data[f"SMA_{SHORT_WINDOW}"].iloc[-1] > data[f"SMA_{LONG_WINDOW}"].iloc[-1]:
        return "BUY"
    elif data[f"SMA_{SHORT_WINDOW}"].iloc[-1] < data[f"SMA_{LONG_WINDOW}"].iloc[-1]:
        return "SELL"
    else:
        return "HOLD"

def check_balance(symbol):
    account_balance = client.get_asset_balance(asset=symbol)
    logging.info(f"Saldo para {symbol}: Livre = {account_balance['free']}, Bloqueado = {account_balance['locked']}")
    return float(account_balance['free'])

def calculate_quantity(symbol, usdt_balance):
    price = float(client.get_symbol_ticker(symbol=symbol)['price'])
    allocated_balance = usdt_balance * (RISK_PERCENTAGE / 100)
    max_quantity = allocated_balance / price
    min_qty, step_size = get_lot_size(symbol)
    adjusted_quantity = round_quantity(max_quantity, step_size)
    if adjusted_quantity < min_qty:
        logging.error(f"Saldo insuficiente para comprar a quantidade mínima ({min_qty}). Máximo permitido: {adjusted_quantity}.")
        return 0
    return adjusted_quantity

def get_lot_size(symbol):
    exchange_info = client.get_exchange_info()
    for s in exchange_info['symbols']:
        if s['symbol'] == symbol:
            for f in s['filters']:
                if f['filterType'] == 'LOT_SIZE':
                    min_qty = float(f['minQty'])
                    step_size = float(f['stepSize'])
                    return min_qty, step_size
    return None, None

def get_min_notional(symbol):
    exchange_info = client.get_exchange_info()
    for s in exchange_info['symbols']:
        if s['symbol'] == symbol:
            for f in s['filters']:
                if f['filterType'] == 'MIN_NOTIONAL':
                    return float(f['minNotional'])
    return 0

def check_notional(symbol, quantity):
    price = float(client.get_symbol_ticker(symbol=symbol)['price'])
    notional = price * quantity
    min_notional = get_min_notional(symbol)
    logging.info(f"Valor da ordem calculado: {notional}, NOTIONAL mínimo: {min_notional}")
    return notional >= min_notional

def round_quantity(quantity, step_size):
    return quantity - (quantity % step_size)

def can_execute_trade(usdt_balance, symbol):
    price = float(client.get_symbol_ticker(symbol=symbol)['price'])
    min_qty, step_size = get_lot_size(symbol)
    min_notional = get_min_notional(symbol)
    required_notional = price * min_qty
    return usdt_balance >= required_notional

def validate_symbol(symbol):
    exchange_info = client.get_exchange_info()
    if not any(s['symbol'] == symbol for s in exchange_info['symbols']):
        raise ValueError(f"O par {symbol} não está disponível na Binance.")
    logging.info(f"Par {symbol} validado com sucesso.")

def execute_trade(decision):
    try:
        min_qty, step_size = get_lot_size(SYMBOL)
        if min_qty is None or step_size is None:
            logging.error("Não foi possível obter as restrições de LOT_SIZE.")
            return

        if decision == "BUY":
            usdt_balance = check_balance("USDT")
            if not can_execute_trade(usdt_balance, SYMBOL):
                logging.error("Saldo insuficiente para executar a compra com valor mínimo prático.")
                return

            usdt_balance -= 0.01  # Margem de segurança
            max_quantity = calculate_quantity(SYMBOL, usdt_balance)
            if max_quantity == 0 or not check_notional(SYMBOL, max_quantity):
                logging.error("Ordem de compra não atende aos requisitos de NOTIONAL mínimo.")
                return

            logging.info(f"Quantidade ajustada para compra: {max_quantity}")
            order = client.order_market_buy(
                symbol=SYMBOL,
                quantity=max_quantity
            )
            logging.info(f"Compra realizada: {order}")

            stop_loss_price = float(client.get_symbol_ticker(symbol=SYMBOL)['price']) * (1 - STOP_LOSS_PERCENTAGE / 100)
            take_profit_price = float(client.get_symbol_ticker(symbol=SYMBOL)['price']) * (1 + TAKE_PROFIT_PERCENTAGE / 100)
            logging.info(f"Stop-Loss definido em: {stop_loss_price}, Take-Profit definido em: {take_profit_price}")

        elif decision == "SELL":
            crypto_balance = check_balance(SYMBOL.replace("USDT", ""))
            adjusted_quantity = round_quantity(crypto_balance, step_size)
            if adjusted_quantity < min_qty:
                logging.error(f"Quantidade ajustada ({adjusted_quantity}) é menor que a quantidade mínima permitida ({min_qty}). Venda ignorada.")
                time.sleep(600)  # Pausa de 10 minutos para evitar logs repetitivos
                return

            if not check_notional(SYMBOL, adjusted_quantity):
                logging.error("Ordem de venda não atende aos requisitos de NOTIONAL mínimo.")
                time.sleep(600)  # Pausa de 10 minutos para evitar logs repetitivos
                return

            order = client.order_market_sell(
                symbol=SYMBOL,
                quantity=adjusted_quantity
            )
            logging.info(f"Venda realizada: {order}")

    except BinanceAPIException as e:
        logging.error(f"Erro ao executar trade: {e}")

# Validar o par de moedas
validate_symbol(SYMBOL)

# Limpar o arquivo de log
with open("trading_log.txt", "w") as log_file:
    log_file.write("")
logging.info("Arquivo de log limpo.")

# Log das restrições do par
min_qty, step_size = get_lot_size(SYMBOL)
min_notional = get_min_notional(SYMBOL)
logging.info(f"Restrições do par {SYMBOL} - MinQty: {min_qty}, StepSize: {step_size}, MinNotional: {min_notional}")

# Loop principal que mantém o robô operando continuamente
print("Iniciando o robô trader...")
logging.info("Robô iniciado.")
while True:
    try:
        data = fetch_data(SYMBOL, INTERVAL)
        data = calculate_moving_averages(data, SHORT_WINDOW, LONG_WINDOW)
        decision = make_decision(data)
        print(f"Decisão: {decision}")
        logging.info(f"Decisão tomada: {decision}")

        if usdt_balance := check_balance("USDT") < 0.01:
            logging.info("Saldo USDT insuficiente para continuar as operações. Robô pausado.")
            time.sleep(600)  # Aguarda 10 minutos antes de verificar novamente
            continue

        if decision in ["BUY", "SELL"]:
            execute_trade(decision)
        else:
            logging.info("Sem ação necessária.")
        time.sleep(60)
    except Exception as e:
        logging.error(f"Erro no loop principal: {e}")
