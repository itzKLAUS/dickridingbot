# bot.py
# This is the main file for your custom game bot. (New Promo System + Enhanced Battle Output + Forced Battles)

import logging
import random
import os
from datetime import datetime, timedelta
from flask import Flask
from threading import Thread

from telegram import Update, MessageEntity, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, CallbackQueryHandler

import database

# --- Web Server Setup ---
app = Flask(__name__)
@app.route('/')
def home(): return "Bot is running!"
def run(): app.run(host='0.0.0.0',port=int(os.environ.get("PORT", 8080)))
def keep_alive(): Thread(target=run).start()

# --- Configuration ---
BOT_TOKEN = os.environ.get('BOT_TOKEN') # Get token from Heroku config var
OWNER_ID = 2002540917
GROW_COOLDOWN_HOURS = 3
SUCK_LIMIT = 3
SUCK_BONUS = 10 # This is in cm
CHALLENGE_EXPIRY_HOURS = 24
FORCED_BATTLE_THRESHOLD = 3

# --- Bot Setup ---
logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Helper Functions ---
def get_opponent_from_message(message):
    if message.reply_to_message:
        if message.from_user.id == message.reply_to_message.from_user.id:
            return None
        return message.reply_to_message.from_user
    for entity in message.entities:
        if entity.type == MessageEntity.MENTION:
            return message.text[entity.offset + 1 : entity.offset + entity.length]
        if entity.type == MessageEntity.TEXT_MENTION:
            if message.from_user.id == entity.user.id:
                return None
            return entity.user
    return None

def get_or_create_player(user, chat_id):
    """Fetches a player from DB or creates them if they don't exist."""
    player = database.get_player(user.id, chat_id)
    if not player:
        database.upsert_player(user.id, chat_id, user.username, user.first_name, 0, None, 0, None, 0, 0, 0, 0) # Initialize wins, losses, streaks
        player = database.get_player(user.id, chat_id)
    return player

def calculate_win_rate(wins, losses):
    total_battles = wins + losses
    if total_battles > 0:
        return (wins / total_battles) * 100
    return 0.00

# --- Main Command Handlers ---

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        user_name = update.message.from_user.first_name
        welcome_message = (
            f"Welcome, {user_name}! This is the Dick Grow Game, where you grow your dick and compete!\n\n"
            "**Battle System:**\n"
            "`/battle <amount>` - Issues an open challenge.\n"
            "`/battle @user <amount>` - Issues a direct challenge.\n"
            "`/forcebattle @user <amount>` - Issues a forced battle challenge.\n\n"
            "**Commands:**\n"
            "`/grow` - Grow your dick.\n"
            "`/mydick` - Check your current size.\n"
            "`/leaderboard` - See the biggest dicks in the group.\n"
            "`/suck @user` - Suck a dick for a bonus.\n"
            "`/redeem <code>` - Redeem a promo code."
        )
        await update.message.reply_text(welcome_message, parse_mode='Markdown')
    except Exception as e:
        logging.error(f"Error in start_command: {e}")

async def battle_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        if update.message.chat.type == 'private':
            await update.message.reply_text("You can only battle in a group chat!"); return

        challenger_user = update.message.from_user
        chat_id = update.message.chat_id

        try:
            bet_amount = int(context.args[-1])
            if bet_amount <= 0: raise ValueError()
        except (IndexError, ValueError):
            await update.message.reply_text("❗️Invalid format. Use `/battle <amount>` or `/battle @user <amount>`."); return

        challenger_player = get_or_create_player(challenger_user, chat_id)
        if challenger_player['stat_value'] < bet_amount:
            await update.message.reply_text("meh your dick is not big enough for the challenge little guy"); return

        opponent_info = get_opponent_from_message(update.message)

        if opponent_info and isinstance(opponent_info, str) and opponent_info.lower() == (challenger_user.username or "").lower():
            await update.message.reply_text("Aye, little bro, tryna be smart by playing with your own dick, eh?"); return
        if opponent_info is None and len(context.args) > 1:
             await update.message.reply_text("Aye, little bro, tryna be smart by playing with your own dick, eh?"); return

        keyboard, text, opponent_id = [], "", None

        if opponent_info: # Direct Challenge
            if isinstance(opponent_info, str): opponent_player = database.get_player_by_username(opponent_info, chat_id)
            else: opponent_player = get_or_create_player(opponent_info, chat_id)

            if not opponent_player: await update.message.reply_text("Could not find that player. They need to /grow first."); return

            opponent_id = opponent_player['user_id']
            text = (f"⚔️ **Direct Challenge!** ⚔️\n\n"
                    f"{challenger_user.first_name} has challenged {opponent_player['first_name']} to a battle for **{bet_amount}cm**!\n\n"
                    f"_{opponent_player['first_name']}, do you accept?_")

            challenge_id = database.create_challenge(chat_id, challenger_user.id, challenger_user.first_name, bet_amount, opponent_id)
            keyboard.append([InlineKeyboardButton("✅ Accept", callback_data=f"accept_{challenge_id}_{opponent_id}"), InlineKeyboardButton("❌ Decline", callback_data=f"decline_{challenge_id}_{opponent_id}")])

        else: # Open Challenge
            text = (f"⚔️ **Open Challenge!** ⚔️\n\n"
                    f"{challenger_user.first_name} has issued an open challenge to the group for **{bet_amount}cm**!\n\n"
                    f"_Who dares to accept?_")
            challenge_id = database.create_challenge(chat_id, challenger_user.id, challenger_user.first_name, bet_amount)
            keyboard.append([InlineKeyboardButton("💪 Accept Challenge", callback_data=f"accept_{challenge_id}_0")])

        reply_markup = InlineKeyboardMarkup(keyboard)
        sent_message = await update.message.reply_text(text, reply_markup=reply_markup, parse_mode='Markdown')
        database.update_challenge_message_id(challenge_id, sent_message.message_id)
    except Exception as e:
        logging.error(f"Error in battle_command: {e}")

async def forcebattle_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        if update.message.chat.type == 'private':
            await update.message.reply_text("You can only use this command in a group chat!"); return

        initiator_user = update.message.from_user
        chat_id = update.message.chat_id

        try:
            bet_amount = int(context.args[-1])
            if bet_amount <= 0: raise ValueError()
        except (IndexError, ValueError):
            await update.message.reply_text("❗️Invalid format. Use `/forcebattle @user <amount>`."); return

        initiator_player = get_or_create_player(initiator_user, chat_id)
        if initiator_player['stat_value'] < bet_amount:
            await update.message.reply_text("meh your dick is not big enough for the challenge little guy"); return

        opponent_info = get_opponent_from_message(update.message)
        if not opponent_info:
            await update.message.reply_text("You need to mention the user you want to force battle!"); return

        if isinstance(opponent_info, str) and opponent_info.lower() == (initiator_user.username or "").lower():
            await update.message.reply_text("You cannot force battle yourself!"); return
        if isinstance(opponent_info, dict) and opponent_info.get('id') == initiator_user.id:
            await update.message.reply_text("You cannot force battle yourself!"); return

        if isinstance(opponent_info, str):
            target_player = database.get_player_by_username(opponent_info, chat_id)
            target_name = opponent_info
            target_id = None
            if target_player:
                target_id = target_player['user_id']
                target_name = target_player['first_name']
        elif isinstance(opponent_info, dict):
            target_id = opponent_info['id']
            target_name = opponent_info.get('first_name', opponent_info.get('username', 'Unknown'))
            target_player = get_or_create_player(opponent_info, chat_id)
        else:
            await update.message.reply_text("Could not find the target user."); return

        if not target_player:
            await update.message.reply_text("That user hasn't played the game yet. They need to /grow first."); return

        initiator = database.get_player(initiator_user.id, chat_id)
        target = database.get_player(target_id, chat_id)
        bet = bet_amount

        if not initiator or not target:
            await update.message.reply_text("Forced battle cannot proceed due to missing player data.")
            return
        if initiator['stat_value'] < bet:
            await update.message.reply_text(f"{initiator['first_name']} does not have enough size for this forced battle.")
            return

        # Determine winner and loser
        if random.random() < initiator['stat_value'] / (initiator['stat_value'] + target['stat_value']):
            winner, loser = initiator, target
        else:
            winner, loser = target, initiator

        # Update sizes
        new_winner_value = winner['stat_value'] + bet
        new_loser_value = loser['stat_value'] - bet
        database.upsert_player(winner['user_id'], chat_id, winner['username'], winner['first_name'], new_winner_value, winner['last_grow_time'], winner['suck_count'], winner['last_suck_time'], winner['wins'] + 1, winner['losses'], winner['win_streak'] + 1 if winner['user_id'] == initiator_user.id else winner['win_streak'] + 1, max(winner['max_win_streak'], winner['win_streak'] + 1))
        database.upsert_player(loser['user_id'], chat_id, loser['username'], loser['first_name'], new_loser_value, loser['last_grow_time'], loser['suck_count'], loser['last_suck_time'], loser['wins'], loser['losses'] + 1, 0, loser['max_win_streak'])

        # Get updated player data
        updated_winner = database.get_player(winner['user_id'], chat_id)
        updated_loser = database.get_player(loser['user_id'], chat_id)

        # Get leaderboard and positions
        leaderboard_data = database.get_leaderboard(chat_id)
        winner_rank = next((i + 1 for i, player in enumerate(leaderboard_data) if player['user_id'] == updated_winner['user_id']), None)
        loser_rank = next((i + 1 for i, player in enumerate(leaderboard_data) if player['user_id'] == updated_loser['user_id']), None)

        # Calculate win rates
        winner_win_rate = calculate_win_rate(updated_winner['wins'], updated_winner['losses'])
        loser_win_rate = calculate_win_rate(updated_loser['wins'], updated_loser['losses'])

        winner_mention = f"‎{updated_winner['first_name']}‎"
        loser_mention = f"‎{updated_loser['first_name']}‎"
        initiator_mention = f"‎{initiator_user.first_name}‎"
        target_mention = f"‎{target_name}‎"

        if winner['user_id'] == initiator_user.id:
            result_text = (
                f"🔥 **FORCED BATTLE OUTCOME!** 🔥\n\n"
                f"{target_mention} was destroyed by the mighty force of {initiator_mention}'s dick and they are victorious!\n"
                f"His dick is now {updated_winner['stat_value']} cm long.\n"
                f"{loser_mention}'s one is {updated_loser['stat_value']} cm.\n"
                f"The bet was {bet} cm.\n\n"
                f"{winner_mention}'s position in the top is {winner_rank}.\n"
                f"{loser_mention}'s position in the top is {loser_rank}.\n\n"
                f"Win rate of the winner — {winner_win_rate:.2f}%.\n"
                f"His current win streak — {updated_winner['win_streak']}, max win streak — {updated_winner['max_win_streak']}.\n"
                f"Win rate of the loser — {loser_win_rate:.2f}%.\n"
            )
        else:
            result_text = (
                f"🤣 **FORCED BATTLE FAIL!** 🤣\n\n"
                f"{initiator_mention} tried destroying {target_mention} with their dick but miserably failed against their mighty dick!\n"
                f"{winner_mention}'s dick is now {updated_winner['stat_value']} cm long.\n"
                f"{loser_mention}'s one is {updated_loser['stat_value']} cm.\n"
                f"The bet was {bet} cm.\n\n"
                f"{winner_mention}'s position in the top is {winner_rank}.\n"
                f"{loser_mention}'s position in the top is {loser_rank}.\n\n"
                f"Win rate of the winner — {winner_win_rate:.2f}%.\n"
                f"His current win streak — {updated_winner['win_streak']}, max win streak — {updated_winner['max_win_streak']}.\n"
                f"Win rate of the loser — {loser_win_rate:.2f}%.\n"
            )
        await update.message.reply_text(text=result_text, parse_mode='Markdown')
    except Exception as e:
        logging.error(f"Error in forcebattle_command: {e}")

async def button_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        query = update.callback_query
        await query.answer()
        action_parts = query.data.split('_')
        action = action_parts[0]

        if action in ['accept', 'decline']: # Handle regular battle accept/decline
            _, challenge_id_str, target_user_id_str = action_parts
            challenge_id, target_user_id = int(challenge_id_str), int(target_user_id_str)
            challenge = database.get_challenge(challenge_id)
            if not challenge: await query.edit_message_text(text="This challenge is no longer active."); return
            if datetime.now() > datetime.fromisoformat(challenge['creation_time']) + timedelta(hours=CHALLENGE_EXPIRY_HOURS):
                database.deactivate_challenge(challenge_id)
                await query.edit_message_text(text=f"This challenge from {challenge['challenger_name']} has expired."); return

            if action == 'decline':
                if user_who_clicked.id != target_user_id and user_who_clicked.id != challenge['challenger_id']:
                    await query.answer("This is not your decision to make.", show_alert=True); return
                database.deactivate_challenge(challenge_id)
                await query.edit_message_text(text=f"{challenge['challenger_name']}'s challenge was declined by {user_who_clicked.first_name}."); return

            if action == 'accept':
                if target_user_id != 0 and user_who_clicked.id != target_user_id:
                    await query.answer("This challenge was not meant for you.", show_alert=True); return
                if user_who_clicked.id == challenge['challenger_id']:
                    await query.answer("Aye, little bro, tryna be smart by playing with your own dick, eh?", show_alert=True); return

                challenger = database.get_player(challenge['challenger_id'], challenge['chat_id'])
                opponent = database.get_player(user_who_clicked.id, challenge['chat_id']) # Ensure opponent is fetched correctly
                bet = challenge['bet_amount']

                if not opponent or opponent['stat_value'] < bet:
                    await query.answer("You don't have enough size to accept this challenge!", show_alert=True)
                    return
                if not challenger or challenger['stat_value'] < bet:
                    await query.edit_message_text(text=f"Challenge cancelled. {challenger['first_name']} no longer has enough size.")
                    database.deactivate_challenge(challenge_id)
                    return

                database.deactivate_challenge(challenge_id)

                # Determine winner and loser
                if random.random() < challenger['stat_value'] / (challenger['stat_value'] + opponent['stat_value']):
                    winner, loser = challenger, opponent
                else:
                    winner, loser = opponent, challenger

                # Update sizes
                new_winner_value = winner['stat_value'] + bet
                new_loser_value = loser['stat_value'] - bet
                database.upsert_player(winner['user_id'], challenge['chat_id'], winner['username'], winner['first_name'], new_winner_value, winner['last_grow_time'], winner['suck_count'], winner['last_suck_time'], winner['wins'] + 1, winner['losses'], winner['win_streak'] + 1 if winner['user_id'] == challenge['challenger_id'] else winner['win_streak'] + 1, max(winner['max_win_streak'], winner['win_streak'] + 1))
                database.upsert_player(loser['user_id'], challenge['chat_id'], loser['username'], loser['first_name'], new_loser_value, loser['last_grow_time'], loser['suck_count'], loser['last_suck_time'], loser['wins'], loser['losses'] + 1, 0, loser['max_win_streak'])

                # Get updated player data
                updated_winner = database.get_player(winner['user_id'], challenge['chat_id'])
                updated_loser = database.get_player(loser['user_id'], challenge['chat_id'])

                # Get leaderboard and positions
                leaderboard_data = database.get_leaderboard(challenge['chat_id'])
                winner_rank = next((i + 1 for i, player in enumerate(leaderboard_data) if player['user_id'] == updated_winner['user_id']), None)
                loser_rank = next((i + 1 for i, player in enumerate(leaderboard_data) if player['user_id'] == updated_loser['user_id']), None)

                # Calculate win rates
                winner_win_rate = calculate_win_rate(updated_winner['wins'], updated_winner['losses'])
                loser_win_rate = calculate_win_rate(updated_loser['wins'], updated_loser['losses'])

                winner_mention = f"‎{updated_winner['first_name']}‎"
                loser_mention = f"‎{updated_loser['first_name']}‎"

                teasing_messages_winner = [
                    f"What a magnificent dick! {winner_mention} has destroyed {loser_mention}!",
                    f"{winner_mention} showed {loser_mention} who's boss!",
                    f"{winner_mention} just schooled {loser_mention} in the art of dick growth!",
                    f"Bow down to {winner_mention}, the new champion of this battle!",
                    f"{winner_mention}'s dick is so superior, it's almost unfair to {loser_mention}!"
                ]
                teasing_messages_loser = [
                    f"Ouch! {loser_mention}'s dick just got a serious trim!",
                    f"{loser_mention} might wanna `/suck` someone today to recover from that little bitch.",
                    f"Better luck next time, {loser_mention}! Your dick needs more training.",
                    f"Looks like {loser_mention} needs a growth spurt after that loss.",
                    f"Don't worry, {loser_mention}, size isn't everything... or is it?"
                ]

                praise_winner = random.choice(teasing_messages_winner)
                tease_loser = random.choice(teasing_messages_loser)

                result_text = (
                    f"**Battle Result:**\n\n"
                    f"The winner is {winner_mention}! His dick is now {updated_winner['stat_value']} cm long.\n"
                    f"The loser's one is {updated_loser['stat_value']} cm.\n"
                    f"The bet was {bet} cm.\n\n"
                    f"{winner_mention}'s position in the top is {winner_rank}.\n"
                    f"{loser_mention}'s position in the top is {loser_rank}.\n\n"
                    f"Win rate of the winner — {winner_win_rate:.2f}%.\n"
                    f"His current win streak — {updated_winner['win_streak']}, max win streak — {updated_winner['max_win_streak']}.\n"
                    f"Win rate of the loser — {loser_win_rate:.2f}%.\n\n"
                    f"{praise_winner}\n"
                    f"_{tease_loser}_"
                )
                await query.edit_message_text(text=result_text, parse_mode='Markdown')
    except Exception as e:
        logging.error(f"Error in button_callback_handler: {e}")

async def suck_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        if update.message.chat.type == 'private':
            await update.message.reply_text("This command only works in group chats."); return

        user = update.message.from_user
        chat_id = update.message.chat_id

        target_user_info = get_opponent_from_message(update.message)
        if not target_user_info:
            await update.message.reply_text("You need to specify who you want to suck! Use /suck @username or reply to their message."); return

        is_self = False
        if isinstance(target_user_info, str):
            if target_user_info.lower() == (user.username or "").lower(): is_self = True
        elif hasattr(target_user_info, 'id'):
            if target_user_info.id == user.id: is_self = True
        if is_self:
            await update.message.reply_text("Sucking your own dick gives no bonus, silly."); return

        now = datetime.now()
        player = get_or_create_player(user, chat_id)
        suck_count = player['suck_count']
        last_suck_time = datetime.fromisoformat(player['last_suck_time']) if player['last_suck_time'] else None

        if last_suck_time and now < last_suck_time + timedelta(days=1):
            if suck_count >= SUCK_LIMIT:
                time_left = (last_suck_time + timedelta(days=1)) - now; hours, rem = divmod(time_left.seconds, 3600); minutes, _ = divmod(rem, 60)
                await update.message.reply_text(f"You have already sucked {SUCK_LIMIT} dicks today. Try again in {hours}h {minutes}m."); return
        else:
            suck_count = 0

        if isinstance(target_user_info, str):
            target_player = database.get_player_by_username(target_user_info, chat_id)
        else:
            target_player = get_or_create_player(target_user_info, chat_id)

        if not target_player:
            await update.message.reply_text("That user hasn't played the game yet. They need to /grow first."); return

        new_suck_count = suck_count + 1
        new_value = player['stat_value'] + SUCK_BONUS
        database.upsert_player(user.id, chat_id, user.username, user.first_name, new_value, player['last_grow_time'], new_suck_count, now.isoformat())

        sucker_name = user.first_name.replace('[', '\\[').replace(']', '\\]')
        sucked_name = target_player['first_name'].replace('[', '\\[').replace(']', '\\]')
        sucker_mention = f"[{sucker_name}](tg://user?id={user.id})"
        sucked_mention = f"[{sucked_name}](tg://user?id={target_player['user_id']})"
        final_message = (f"{sucker_mention} loves sucking those juicy dicks\\. They just sucked {sucked_mention}'s dick and got a {SUCK_BONUS}cm bonus\\!\n\n"
                         f"Their size is now {new_value}cm\\.\n"
                         f"They have {SUCK_LIMIT - new_suck_count} sucks left today\\.")
        await update.message.reply_text(final_message, parse_mode='MarkdownV2')
    except Exception as e:
        logging.error(f"Error in suck_command: {e}")

async def grow_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        if update.message.chat.type == 'private': await update.message.reply_text("This game is designed for group chats!"); return
        user = update.message.from_user
        chat_id = update.message.chat_id
        player = get_or_create_player(user, chat_id)
        now = datetime.now()

        if player['last_grow_time']:
            last_grow_time = datetime.fromisoformat(player['last_grow_time'])
            if now < last_grow_time + timedelta(hours=GROW_COOLDOWN_HOURS):
                time_left = (last_grow_time + timedelta(hours=GROW_COOLDOWN_HOURS)) - now; hours, rem = divmod(time_left.seconds, 3600); minutes, _ = divmod(rem, 60)
                await update.message.reply_text(f"⏳ You can use /grow again in {hours}h {minutes}m."); return

        growth = random.randint(1, 10)
        new_value = player['stat_value'] + growth
        database.upsert_player(user.id, chat_id, user.username, user.first_name, new_value, now.isoformat(), player['suck_count'], player['last_suck_time'])
        await update.message.reply_text(f"💪 {user.first_name}, your dick grew by {growth}cm! It is now {new_value}cm.")
    except Exception as e:
        logging.error(f"Error in grow_command: {e}")

async def mydick_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        if update.message.chat.type == 'private': await update.message.reply_text("You can only check your size in a group chat."); return
        user, chat_id = update.message.from_user, update.message.chat_id
        player = get_or_create_player(user, chat_id)
        if player['stat_value'] > 0: await update.message.reply_text(f"📊 {player['first_name']}, your current size is {player['stat_value']}cm.")
        else: await update.message.reply_text("You have no size in this chat yet! Use /grow to get started.")
    except Exception as e:
        logging.error(f"Error in mydick_command: {e}")

async def leaderboard_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        if update.message.chat.type == 'private': await update.message.reply_text("Leaderboards are only available in group chats."); return
        chat_id = update.message.chat_id; leaderboard_data = database.get_leaderboard(chat_id)
        if not leaderboard_data: await update.message.reply_text("The leaderboard is empty. Use /grow to get on it!"); return
        leaderboard_text = "🏆 Leaderboard 🏆\n\n";
        for i, row in enumerate(leaderboard_data):
            rank_emoji = ["🥇", "🥈", "🥉"]; leaderboard_text += f"{rank_emoji[i] if i < 3 else f'  {i+1}.'} "; leaderboard_text += f"{row['first_name']}: {row['stat_value']}cm\n"
        await update.message.reply_text(leaderboard_text)
    except Exception as e:
        logging.error(f"Error in leaderboard_command: {e}")

async def promo_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        user_id = update.message.from_user.id
        if user_id != OWNER_ID:
            await update.message.reply_text("You are not authorized to use this command.")
            return
        try:
            code, value = context.args[0], int(context.args[1])
            if value <= 0: raise ValueError()
        except (IndexError, ValueError):
            await update.message.reply_text("Invalid format. Use: /promo <code> <value>")
            return
        database.create_promo_code(code, value)
        await update.message.reply_text(f"✅ Promo code '{code}' created with a value of {value}cm.")
    except Exception as e:
        logging.error(f"Error in promo_command: {e}")

async def redeem_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        if update.message.chat.type == 'private': await update.message.reply_text("You must be in a group chat to redeem codes.")
        return

        user, chat_id = update.message.from_user, update.message.chat_id
        try:
            code_to_redeem = context.args[0]
        except IndexError:
            await update.message.reply_text("Please provide a code. Use: /redeem <code>")
            return

        promo_code = database.get_promo_code(code_to_redeem)
        if not promo_code:
            await update.message.reply_text("That promo code doesn't exist.")
            return

        if database.has_user_redeemed_code(user.id, code_to_redeem):
            await update.message.reply_text("You have already redeemed this promo code.")
            return

        player = get_or_create_player(user, chat_id)
        new_value = player['stat_value'] + promo_code['value']
        database.upsert_player(user.id, chat_id, user.username, user.first_name, new_value, player['last_grow_time'], player['suck_count'], player['last_suck_time'])
        database.mark_code_as_redeemed(user.id, code_to_redeem)
        await update.message.reply_text(f"🎉 Success! You redeemed '{code_to_redeem}' for a bonus of {promo_code['value']}cm!\nYour new size is {new_value}cm.")
    except Exception as e:
        logging.error(f"Error in redeem_command: {e}")

async def button_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        query = update.callback_query
        await query.answer()
        action_parts = query.data.split('_')
        action = action_parts[0]

        if action in ['accept', 'decline']: # Handle regular battle accept/decline
            _, challenge_id_str, target_user_id_str = action_parts
            challenge_id, target_user_id = int(challenge_id_str), int(target_user_id)
            challenge = database.get_challenge(challenge_id)
            if not challenge: await query.edit_message_text(text="This challenge is no longer active."); return
            if datetime.now() > datetime.fromisoformat(challenge['creation_time']) + timedelta(hours=CHALLENGE_EXPIRY_HOURS):
                database.deactivate_challenge(challenge_id)
                await query.edit_message_text(text=f"This challenge from {challenge['challenger_name']} has expired."); return

            if action == 'decline':
                if user_who_clicked.id != target_user_id and user_who_clicked.id != challenge['challenger_id']:
                    await query.answer("This is not your decision to make.", show_alert=True); return
                database.deactivate_challenge(challenge_id)
                await query.edit_message_text(text=f"{challenge['challenger_name']}'s challenge was declined by {user_who_clicked.first_name}."); return

            if action == 'accept':
                if target_user_id != 0 and user_who_clicked.id != target_user_id:
                    await query.answer("This challenge was not meant for you.", show_alert=True); return
                if user_who_clicked.id == challenge['challenger_id']:
                    await query.answer("Aye, little bro, tryna be smart by playing with your own dick, eh?", show_alert=True); return

                challenger = database.get_player(challenge['challenger_id'], challenge['chat_id'])
                opponent = database.get_player(user_who_clicked.id, challenge['chat_id']) # Ensure opponent is fetched correctly
                bet = challenge['bet_amount']

                if not opponent or opponent['stat_value'] < bet:
                    await query.answer("You don't have enough size to accept this challenge!", show_alert=True)
                    return
                if not challenger or challenger['stat_value'] < bet:
                    await query.edit_message_text(text=f"Challenge cancelled. {challenger['first_name']} no longer has enough size.")
                    database.deactivate_challenge(challenge_id)
                    return

                database.deactivate_challenge(challenge_id)

                # Determine winner and loser
                if random.random() < challenger['stat_value'] / (challenger['stat_value'] + opponent['stat_value']):
                    winner, loser = challenger, opponent
                else:
                    winner, loser = opponent, challenger

                # Update sizes
                new_winner_value = winner['stat_value'] + bet
                new_loser_value = loser['stat_value'] - bet
                database.upsert_player(winner['user_id'], challenge['chat_id'], winner['username'], winner['first_name'], new_winner_value, winner['last_grow_time'], winner['suck_count'], winner['last_suck_time'], winner['wins'] + 1, winner['losses'], winner['win_streak'] + 1 if winner['user_id'] == challenge['challenger_id'] else winner['win_streak'] + 1, max(winner['max_win_streak'], winner['win_streak'] + 1))
                database.upsert_player(loser['user_id'], challenge['chat_id'], loser['username'], loser['first_name'], new_loser_value, loser['last_grow_time'], loser['suck_count'], loser['last_suck_time'], loser['wins'], loser['losses'] + 1, 0, loser['max_win_streak'])

                # Get updated player data
                updated_winner = database.get_player(winner['user_id'], challenge['chat_id'])
                updated_loser = database.get_player(loser['user_id'], challenge['chat_id'])

                # Get leaderboard and positions
                leaderboard_data = database.get_leaderboard(challenge['chat_id'])
                winner_rank = next((i + 1 for i, player in enumerate(leaderboard_data) if player['user_id'] == updated_winner['user_id']), None)
                loser_rank = next((i + 1 for i, player in enumerate(leaderboard_data) if player['user_id'] == updated_loser['user_id']), None)

                # Calculate win rates
                winner_win_rate = calculate_win_rate(updated_winner['wins'], updated_winner['losses'])
                loser_win_rate = calculate_win_rate(updated_loser['wins'], updated_loser['losses'])

                winner_mention = f"‎{updated_winner['first_name']}‎"
                loser_mention = f"‎{updated_loser['first_name']}‎"

                teasing_messages_winner = [
                    f"What a magnificent dick! {winner_mention} has destroyed {loser_mention}!",
                    f"{winner_mention} showed {loser_mention} who's boss!",
                    f"{winner_mention} just schooled {loser_mention} in the art of dick growth!",
                    f"Bow down to {winner_mention}, the new champion of this battle!",
                    f"{winner_mention}'s dick is so superior, it's almost unfair to {loser_mention}!"
                ]
                teasing_messages_loser = [
                    f"Ouch! {loser_mention}'s dick just got a serious trim!",
                    f"{loser_mention} might wanna `/suck` someone today to recover from that little bitch.",
                    f"Better luck next time, {loser_mention}! Your dick needs more training.",
                    f"Looks like {loser_mention} needs a growth spurt after that loss.",
                    f"Don't worry, {loser_mention}, size isn't everything... or is it?"
                ]

                praise_winner = random.choice(teasing_messages_winner)
                tease_loser = random.choice(teasing_messages_loser)

                result_text = (
                    f"**Battle Result:**\n\n"
                    f"The winner is {winner_mention}! His dick is now {updated_winner['stat_value']} cm long.\n"
                    f"The loser's one is {updated_loser['stat_value']} cm.\n"
                    f"The bet was {bet} cm.\n\n"
                    f"{winner_mention}'s position in the top is {winner_rank}.\n"
                    f"{loser_mention}'s position in the top is {loser_rank}.\n\n"
                    f"Win rate of the winner — {winner_win_rate:.2f}%.\n"
                    f"His current win streak — {updated_winner['win_streak']}, max win streak — {updated_winner['max_win_streak']}.\n"
                    f"Win rate of the loser — {loser_win_rate:.2f}%.\n\n"
                    f"{praise_winner}\n"
                    f"_{tease_loser}_"
                )
                await query.edit_message_text(text=result_text, parse_mode='Markdown')
    except Exception as e:
        logging.error(f"Error in button_callback_handler: {e}")

async def suck_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        if update.message.chat.type == 'private':
            await update.message.reply_text("This command only works in group chats."); return

        user = update.message.from_user
        chat_id = update.message.chat_id

        target_user_info = get_opponent_from_message(update.message)
        if not target_user_info:
            await update.message.reply_text("You need to specify who you want to suck! Use /suck @username or reply to their message."); return

        is_self = False
        if isinstance(target_user_info, str):
            if target_user_info.lower() == (user.username or "").lower(): is_self = True
        elif hasattr(target_user_info, 'id'):
            if target_user_info.id == user.id: is_self = True
        if is_self:
            await update.message.reply_text("Sucking your own dick gives no bonus, silly."); return

        now = datetime.now()
        player = get_or_create_player(user, chat_id)
        suck_count = player['suck_count']
        last_suck_time = datetime.fromisoformat(player['last_suck_time']) if player['last_suck_time'] else None

        if last_suck_time and now < last_suck_time + timedelta(days=1):
            if suck_count >= SUCK_LIMIT:
                time_left = (last_suck_time + timedelta(days=1)) - now; hours, rem = divmod(time_left.seconds, 3600); minutes, _ = divmod(rem, 60)
                await update.message.reply_text(f"You have already sucked {SUCK_LIMIT} dicks today. Try again in {hours}h {minutes}m."); return
        else:
            suck_count = 0

        if isinstance(target_user_info, str):
            target_player = database.get_player_by_username(target_user_info, chat_id)
        else:
            target_player = get_or_create_player(target_user_info, chat_id)

        if not target_player:
            await update.message.reply_text("That user hasn't played the game yet. They need to /grow first."); return

        new_suck_count = suck_count + 1
        new_value = player['stat_value'] + SUCK_BONUS
        database.upsert_player(user.id, chat_id, user.username, user.first_name, new_value, player['last_grow_time'], new_suck_count, now.isoformat())

        sucker_name = user.first_name.replace('[', '\\[').replace(']', '\\]')
        sucked_name = target_player['first_name'].replace('[', '\\[').replace(']', '\\]')
        sucker_mention = f"[{sucker_name}](tg://user?id={user.id})"
        sucked_mention = f"[{sucked_name}](tg://user?id={target_player['user_id']})"
        final_message = (f"{sucker_mention} loves sucking those juicy dicks\\. They just sucked {sucked_mention}'s dick and got a {SUCK_BONUS}cm bonus\\!\n\n"
                         f"Their size is now {new_value}cm\\.\n"
                         f"They have {SUCK_LIMIT - new_suck_count} sucks left today\\.")
        await update.message.reply_text(final_message, parse_mode='MarkdownV2')
    except Exception as e:
        logging.error(f"Error in suck_command: {e}")

async def grow_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        if update.message.chat.type == 'private': await update.message.reply_text("This game is designed for group chats!"); return
        user = update.message.from_user
        chat_id = update.message.chat_id
        player = get_or_create_player(user, chat_id)
        now = datetime.now()

        if player['last_grow_time']:
            last_grow_time = datetime.fromisoformat(player['last_grow_time'])
            if now < last_grow_time + timedelta(hours=GROW_COOLDOWN_HOURS):
                time_left = (last_grow_time + timedelta(hours=GROW_COOLDOWN_HOURS)) - now; hours, rem = divmod(time_left.seconds, 3600); minutes, _ = divmod(rem, 60)
                await update.message.reply_text(f"⏳ You can use /grow again in {hours}h {minutes}m."); return

        growth = random.randint(1, 10)
        new_value = player['stat_value'] + growth
        database.upsert_player(user.id, chat_id, user.username, user.first_name, new_value, now.isoformat(), player['suck_count'], player['last_suck_time'])
        await update.message.reply_text(f"💪 {user.first_name}, your dick grew by {growth}cm! It is now {new_value}cm.")
    except Exception as e:
        logging.error(f"Error in grow_command: {e}")

async def mydick_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        if update.message.chat.type == 'private': await update.message.reply_text("You can only check your size in a group chat."); return
        user, chat_id = update.message.from_user, update.message.chat_id
        player = get_or_create_player(user, chat_id)
        if player['stat_value'] > 0: await update.message.reply_text(f"📊 {player['first_name']}, your current size is {player['stat_value']}cm.")
        else: await update.message.reply_text("You have no size in this chat yet! Use /grow to get started.")
    except Exception as e:
        logging.error(f"Error in mydick_command: {e}")

async def leaderboard_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        if update.message.chat.type == 'private': await update.message.reply_text("Leaderboards are only available in group chats."); return
        chat_id = update.message.chat_id; leaderboard_data = database.get_leaderboard(chat_id)
        if not leaderboard_data: await update.message.reply_text("The leaderboard is empty. Use /grow to get on it!"); return
        leaderboard_text = "🏆 Leaderboard 🏆\n\n";
        for i, row in enumerate(leaderboard_data):
            rank_emoji = ["🥇", "🥈", "🥉"]; leaderboard_text += f"{rank_emoji[i] if i < 3 else f'  {i+1}.'} "; leaderboard_text += f"{row['first_name']}: {row['stat_value']}cm\n"
        await update.message.reply_text(leaderboard_text)
    except Exception as e:
        logging.error(f"Error in leaderboard_command: {e}")

async def promo_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        user_id = update.message.from_user.id
        if user_id != OWNER_ID:
            await update.message.reply_text("You are not authorized to use this command.")
            return
        try:
            code, value = context.args[0], int(context.args[1])
            if value <= 0: raise ValueError()
        except (IndexError, ValueError):
            await update.message.reply_text("Invalid format. Use: /promo <code> <value>")
            return
        database.create_promo_code(code, value)
        await update.message.reply_text(f"✅ Promo code '{code}' created with a value of {value}cm.")
    except Exception as e:
        logging.error(f"Error in promo_command: {e}")

async def redeem_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        if update.message.chat.type == 'private': await update.message.reply_text("You must be in a group chat to redeem codes.")
        return

        user, chat_id = update.message.from_user, update.message.chat_id
        try:
            code_to_redeem = context.args[0]
        except IndexError:
            await update.message.reply_text("Please provide a code. Use: /redeem <code>")
            return

        promo_code = database.get_promo_code(code_to_redeem)
        if not promo_code:
            await update.message.reply_text("That promo code doesn't exist.")
            return

        if database.has_user_redeemed_code(user.id, code_to_redeem):
            await update.message.reply_text("You have already redeemed this promo code.")
            return

        player = get_or_create_player(user, chat_id)
        new_value = player['stat_value'] + promo_code['value']
        database.upsert_player(user.id, chat_id, user.username, user.first_name, new_value, player['last_grow_time'], player['suck_count'], player['last_suck_time'])
        database.mark_code_as_redeemed(user.id, code_to_redeem)
        await update.message.reply_text(f"🎉 Success! You redeemed '{code_to_redeem}' for a bonus of {promo_code['value']}cm!\nYour new size is {new_value}cm.")
    except Exception as e:
        logging.error(f"Error in redeem_command: {e}")

async def button_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        query = update.callback_query
        await query.answer()
        action_parts = query.data.split('_')
        action = action_parts[0]

        if action in ['accept', 'decline']: # Handle regular battle accept/decline
            _, challenge_id_str, target_user_id_str = action_parts
            challenge_id, target_user_id = int(challenge_id_str), int(target_user_id_str)
            challenge = database.get_challenge(challenge_id)
            if not challenge: await query.edit_message_text(text="This challenge is no longer active."); return
            if datetime.now() > datetime.fromisoformat(challenge['creation_time']) + timedelta(hours=CHALLENGE_EXPIRY_HOURS):
                database.deactivate_challenge(challenge_id)
                await query.edit_message_text(text=f"This challenge from {challenge['challenger_name']} has expired."); return

            if action == 'decline':
                if user_who_clicked.id != target_user_id and user_who_clicked.id != challenge['challenger_id']:
                    await query.answer("This is not your decision to make.", show_alert=True); return
                database.deactivate_challenge(challenge_id)
                await query.edit_message_text(text=f"{challenge['challenger_name']}'s challenge was declined by {user_who_clicked.first_name}."); return

            if action == 'accept':
                if target_user_id != 0 and user_who_clicked.id != target_user_id:
                    await query.answer("This challenge was not meant for you.", show_alert=True); return
                if user_who_clicked.id == challenge['challenger_id']:
                    await query.answer("Aye, little bro, tryna be smart by playing with your own dick, eh?", show_alert=True); return

                challenger = database.get_player(challenge['challenger_id'], challenge['chat_id'])
                opponent = database.get_player(user_who_clicked.id, challenge['chat_id']) # Ensure opponent is fetched correctly
                bet = challenge['bet_amount']

                if not opponent or opponent['stat_value'] < bet:
                    await query.answer("You don't have enough size to accept this challenge!", show_alert=True)
                    return
                if not challenger or challenger['stat_value'] < bet:
                    await query.edit_message_text(text=f"Challenge cancelled. {challenger['first_name']} no longer has enough size.")
                    database.deactivate_challenge(challenge_id)
                    return

                database.deactivate_challenge(challenge_id)

                # Determine winner and loser
                if random.random() < challenger['stat_value'] / (challenger['stat_value'] + opponent['stat_value']):
                    winner, loser = challenger, opponent
                else:
                    winner, loser = opponent, challenger

                # Update sizes
                new_winner_value = winner['stat_value'] + bet
                new_loser_value = loser['stat_value'] - bet
                database.upsert_player(winner['user_id'], challenge['chat_id'], winner['username'], winner['first_name'], new_winner_value, winner['last_grow_time'], winner['suck_count'], winner['last_suck_time'], winner['wins'] + 1, winner['losses'], winner['win_streak'] + 1 if winner['user_id'] == challenge['challenger_id'] else winner['win_streak'] + 1, max(winner['max_win_streak'], winner['win_streak'] + 1))
                database.upsert_player(loser['user_id'], challenge['chat_id'], loser['username'], loser['first_name'], new_loser_value, loser['last_grow_time'], loser['suck_count'], loser['last_suck_time'], loser['wins'], loser['losses'] + 1, 0, loser['max_win_streak'])

                # Get updated player data
                updated_winner = database.get_player(winner['user_id'], challenge['chat_id'])
                updated_loser = database.get_player(loser['user_id'], challenge['chat_id'])

                # Get leaderboard and positions
                leaderboard_data = database.get_leaderboard(challenge['chat_id'])
                winner_rank = next((i + 1 for i, player in enumerate(leaderboard_data) if player['user_id'] == updated_winner['user_id']), None)
                loser_rank = next((i + 1 for i, player in enumerate(leaderboard_data) if player['user_id'] == updated_loser['user_id']), None)

                # Calculate win rates
                winner_win_rate = calculate_win_rate(updated_winner['wins'], updated_winner['losses'])
                loser_win_rate = calculate_win_rate(updated_loser['wins'], updated_loser['losses'])

                winner_mention = f"‎{updated_winner['first_name']}‎"
                loser_mention = f"‎{updated_loser['first_name']}‎"

                teasing_messages_winner = [
                    f"What a magnificent dick! {winner_mention} has destroyed {loser_mention}!",
                    f"{winner_mention} showed {loser_mention} who's boss!",
                    f"{winner_mention} just schooled {loser_mention} in the art of dick growth!",
                    f"Bow down to {winner_mention}, the new champion of this battle!",
                    f"{winner_mention}'s dick is so superior, it's almost unfair to {loser_mention}!"
                ]
                teasing_messages_loser = [
                    f"Ouch! {loser_mention}'s dick just got a serious trim!",
                    f"{loser_mention} might wanna `/suck` someone today to recover from that little bitch.",
                    f"Better luck next time, {loser_mention}! Your dick needs more training.",
                    f"Looks like {loser_mention} needs a growth spurt after that loss.",
                    f"Don't worry, {loser_mention}, size isn't everything... or is it?"
                ]

                praise_winner = random.choice(teasing_messages_winner)
                tease_loser = random.choice(teasing_messages_loser)

                result_text = (
                    f"**Battle Result:**\n\n"
                    f"The winner is {winner_mention}! His dick is now {updated_winner['stat_value']} cm long.\n"
                    f"The loser's one is {updated_loser['stat_value']} cm.\n"
                    f"The bet was {bet} cm.\n\n"
                    f"{winner_mention}'s position in the top is {winner_rank}.\n"
                    f"{loser_mention}'s position in the top is {loser_rank}.\n\n"
                    f"Win rate of the winner — {winner_win_rate:.2f}%.\n"
                    f"His current win streak — {updated_winner['win_streak']}, max win streak — {updated_winner['max_win_streak']}.\n"
                    f"Win rate of the loser — {loser_win_rate:.2f}%.\n\n"
                    f"{praise_winner}\n"
                    f"_{tease_loser}_"
                )
                await query.edit_message_text(text=result_text, parse_mode='Markdown')
    except Exception as e:
        logging.error(f"Error in button_callback_handler: {e}")

async def suck_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        if update.message.chat.type == 'private':
            await update.message.reply_text("This command only works in group chats."); return

        user = update.message.from_user
        chat_id = update.message.chat_id

        target_user_info = get_opponent_from_message(update.message)
        if not target_user_info:
            await update.message.reply_text("You need to specify who you want to suck! Use /suck @username or reply to their message."); return

        is_self = False
        if isinstance(target_user_info, str):
            if target_user_info.lower() == (user.username or "").lower(): is_self = True
        elif hasattr(target_user_info, 'id'):
            if target_user_info.id == user.id: is_self = True
        if is_self:
            await update.message.reply_text("Sucking your own dick gives no bonus, silly."); return

        now = datetime.now()
        player = get_or_create_player(user, chat_id)
        suck_count = player['suck_count']
        last_suck_time = datetime.fromisoformat(player['last_suck_time']) if player['last_suck_time'] else None

        if last_suck_time and now < last_suck_time + timedelta(days=1):
            if suck_count >= SUCK_LIMIT:
                time_left = (last_suck_time + timedelta(days=1)) - now; hours, rem = divmod(time_left.seconds, 3600); minutes, _ = divmod(rem, 60)
                await update.message.reply_text(f"You have already sucked {SUCK_LIMIT} dicks today. Try again in {hours}h {minutes}m."); return
        else:
            suck_count = 0

        if isinstance(target_user_info, str):
            target_player = database.get_player_by_username(target_user_info, chat_id)
        else:
            target_player = get_or_create_player(target_user_info, chat_id)

        if not target_player:
            await update.message.reply_text("That user hasn't played the game yet. They need to /grow first."); return

        new_suck_count = suck_count + 1
        new_value = player['stat_value'] + SUCK_BONUS
        database.upsert_player(user.id, chat_id, user.username, user.first_name, new_value, player['last_grow_time'], new_suck_count, now.isoformat())

        sucker_name = user.first_name.replace('[', '\\[').replace(']', '\\]')
        sucked_name = target_player['first_name'].replace('[', '\\[').replace(']', '\\]')
        sucker_mention = f"[{sucker_name}](tg://user?id={user.id})"
        sucked_mention = f"[{sucked_name}](tg://user?id={target_player['user_id']})"
        final_message = (f"{sucker_mention} loves sucking those juicy dicks\\. They just sucked {sucked_mention}'s dick and got a {SUCK_BONUS}cm bonus\\!\n\n"
                         f"Their size is now {new_value}cm\\.\n"
                         f"They have {SUCK_LIMIT - new_suck_count} sucks left today\\.")
        await update.message.reply_text(final_message, parse_mode='MarkdownV2')
    except Exception as e:
        logging.error(f"Error in suck_command: {e}")

async def grow_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        if update.message.chat.type == 'private': await update.message.reply_text("This game is designed for group chats!"); return
        user = update.message.from_user
        chat_id = update.message.chat_id
        player = get_or_create_player(user, chat_id)
        now = datetime.now()

        if player['last_grow_time']:
            last_grow_time = datetime.fromisoformat(player['last_grow_time'])
            if now < last_grow_time + timedelta(hours=GROW_COOLDOWN_HOURS):
                time_left = (last_grow_time + timedelta(hours=GROW_COOLDOWN_HOURS)) - now; hours, rem = divmod(time_left.seconds, 3600); minutes, _ = divmod(rem, 60)
                await update.message.reply_text(f"⏳ You can use /grow again in {hours}h {minutes}m."); return

        growth = random.randint(1, 10)
        new_value = player['stat_value'] + growth
        database.upsert_player(user.id, chat_id, user.username, user.first_name, new_value, now.isoformat(), player['suck_count'], player['last_suck_time'])
        await update.message.reply_text(f"💪 {user.first_name}, your dick grew by {growth}cm! It is now {new_value}cm.")
    except Exception as e:
        logging.error(f"Error in grow_command: {e}")

async def mydick_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        if update.message.chat.type == 'private': await update.message.reply_text("You can only check your size in a group chat."); return
        user, chat_id = update.message.from_user, update.message.chat_id
        player = get_or_create_player(user, chat_id)
        if player['stat_value'] > 0: await update.message.reply_text(f"📊 {player['first_name']}, your current size is {player['stat_value']}cm.")
        else: await update.message.reply_text("You have no size in this chat yet! Use /grow to get started.")
    except Exception as e:
        logging.error(f"Error in mydick_command: {e}")

async def leaderboard_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        if update.message.chat.type == 'private': await update.message.reply_text("Leaderboards are only available in group chats."); return
        chat_id = update.message.chat_id; leaderboard_data = database.get_leaderboard(chat_id)
        if not leaderboard_data: await update.message.reply_text("The leaderboard is empty. Use /grow to get on it!"); return
        leaderboard_text = "🏆 Leaderboard 🏆\n\n";
        for i, row in enumerate(leaderboard_data):
            rank_emoji = ["🥇", "🥈", "🥉"]; leaderboard_text += f"{rank_emoji[i] if i < 3 else f'  {i+1}.'} "; leaderboard_text += f"{row['first_name']}: {row['stat_value']}cm\n"
        await update.message.reply_text(leaderboard_text)
    except Exception as e:
        logging.error(f"Error in leaderboard_command: {e}")

async def promo_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        user_id = update.message.from_user.id
        if user_id != OWNER_ID:
            await update.message.reply_text("You are not authorized to use this command.")
            return
        try:
            code, value = context.args[0], int(context.args[1])
            if value <= 0: raise ValueError()
        except (IndexError, ValueError):
            await update.message.reply_text("Invalid format. Use: /promo <code> <value>")
            return
        database.create_promo_code(code, value)
        await update.message.reply_text(f"✅ Promo code '{code}' created with a value of {value}cm.")
    except Exception as e:
        logging.error(f"Error in promo_command: {e}")

async def redeem_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        if update.message.chat.type == 'private': await update.message.reply_text("You must be in a group chat to redeem codes.")
        return

        user, chat_id = update.message.from_user, update.message.chat_id
        try:
            code_to_redeem = context.args[0]
        except IndexError:
            await update.message.reply_text("Please provide a code. Use: /redeem <code>")
            return

        promo_code = database.get_promo_code(code_to_redeem)
        if not promo_code:
            await update.message.reply_text("That promo code doesn't exist.")
            return

        if database.has_user_redeemed_code(user.id, code_to_redeem):
            await update.message.reply_text("You have already redeemed this promo code.")
            return

        player = get_or_create_player(user, chat_id)
        new_value = player['stat_value'] + promo_code['value']
        database.upsert_player(user.id, chat_id, user.username, user.first_name, new_value, player['last_grow_time'], player['suck_count'], player['last_suck_time'])
        database.mark_code_as_redeemed(user.id, code_to_redeem)
        await update.message.reply_text(f"🎉 Success! You redeemed '{code_to_redeem}' for a bonus of {promo_code['value']}cm!\nYour new size is {new_value}cm.")
    except Exception as e:
        logging.error(f"Error in redeem_command: {e}")

def main() -> None:
    database.init_db()
    keep_alive()
    try:
        print("Initializing Telegram bot application...")
        application = Application.builder().token(BOT_TOKEN).build()
        print("Telegram bot application initialized.")

        application.add_handler(CommandHandler("start", start_command))
        application.add_handler(CommandHandler("grow", grow_command))
        application.add_handler(CommandHandler("mydick", mydick_command))
        application.add_handler(CommandHandler("leaderboard", leaderboard_command))
        application.add_handler(CommandHandler("suck", suck_command))
        application.add_handler(CommandHandler("battle", battle_command))
        application.add_handler(CommandHandler("forcebattle", forcebattle_command))
        application.add_handler(CommandHandler("promo", promo_command))
        application.add_handler(CommandHandler("redeem", redeem_command))
        application.add_handler(CallbackQueryHandler(button_callback_handler))

        print("Starting Telegram bot polling in a separate thread...")
        threading.Thread(target=application.run_polling).start()
        print("Telegram bot polling thread started.")

    except Exception as e:
        logging.error(f"Error during bot startup: {e}")

if __name__ == "__main__":
    main()



