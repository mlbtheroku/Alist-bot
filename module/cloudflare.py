# -*- coding: UTF-8 -*-

import asyncio
import concurrent.futures
import concurrent.futures
import contextlib
import datetime
import json
import logging
import requests
from apscheduler.triggers.cron import CronTrigger
from pyrogram import filters, Client
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from api.alist_api import storage_list, storage_enable, storage_disable, storage_update
from api.cloudflare_api import list_zones, list_filters, graphql_api
from bot import admin_yz
from config.config import nodee, cronjob, cloudflare_cfg, chat_data, write_config, admin
from tool.handle_exception import handle_exception
from tool.pybyte import pybyte
from tool.scheduler_manager import aps


return_button = [
    InlineKeyboardButton('↩️返回菜单', callback_data='cf_return'),
    InlineKeyboardButton('❌关闭菜单', callback_data='cf_close'),
]


def btn():
    return [
        [InlineKeyboardButton('⚙️CF节点管理', callback_data='⚙️')],
        [
            InlineKeyboardButton('👀查看节点', callback_data='cf_menu_node_status'),
            InlineKeyboardButton('📅通知设置', callback_data='cf_menu_cronjob'),
            InlineKeyboardButton('🆔账号管理', callback_data='cf_menu_account'),
        ],
        [
            InlineKeyboardButton('⚡️功能开关', callback_data='⚡️'),
        ],
        [
            InlineKeyboardButton(
                '✅节点状态监控' if cronjob()['status_push'] else '❎节点状态监控',
                callback_data='status_push_off'
                if cronjob()['status_push']
                else 'status_push_on',
            ),
            InlineKeyboardButton(
                '✅每日流量统计' if cronjob()['bandwidth_push'] else '❎每日流量统计',
                callback_data='bandwidth_push_off'
                if cronjob()['bandwidth_push']
                else 'bandwidth_push_on',
            ),
        ],
        [
            InlineKeyboardButton(
                '✅自动管理存储' if cronjob()['storage_mgmt'] else '❎自动管理存储',
                callback_data='storage_mgmt_off'
                if cronjob()['storage_mgmt']
                else 'storage_mgmt_on',
            ),
            InlineKeyboardButton(
                '✅自动切换节点' if cronjob()['auto_switch_nodes'] else '❎自动切换节点',
                callback_data='auto_switch_nodes_off'
                if cronjob()['auto_switch_nodes']
                else 'auto_switch_nodes_on',
            ),
        ],
        [
            InlineKeyboardButton('❌关闭菜单', callback_data='cf_close'),
        ],
    ]


bandwidth_button_a = [
    InlineKeyboardButton('🟢---', callback_data='gns_total_bandwidth'),
    InlineKeyboardButton('🔴---', callback_data='gns_total_bandwidth'),
    InlineKeyboardButton('⭕️---', callback_data='gns_total_bandwidth'),
]
bandwidth_button_b = [
    InlineKeyboardButton(
        '📈总请求：---', callback_data='gns_total_bandwidth'
    ),
    InlineKeyboardButton(
        '📊总带宽：---', callback_data='gns_total_bandwidth'
    ),
]
bandwidth_button_c = [
    InlineKeyboardButton('🔙上一天', callback_data='gns_status_up'),
    InlineKeyboardButton('---', callback_data='gns_status_calendar'),
    InlineKeyboardButton('下一天🔜', callback_data='gns_status_down'),
]

# 获取节点状态线程池
thread_pool = concurrent.futures.ThreadPoolExecutor(max_workers=20)


#####################################################################################
#####################################################################################
# 按钮回调
# 菜单按钮回调
@Client.on_callback_query(filters.regex('^cf_'))
async def cf_button_callback(client, message):
    query = message.data
    if query == 'cf_close':
        chat_data["account_add"] = False
        chat_id = message.message.chat.id
        message_id = message.message.id
        await client.edit_message_text(chat_id=chat_id,
                                       message_id=message_id,
                                       text='已退出『节点管理』')
    elif query == 'cf_menu_account':
        await account(client, message)
    elif query == 'cf_menu_cronjob':
        await cronjob_set(client, message)
    elif query == 'cf_menu_node_status':
        chat_data['node_status_day'] = 0
        thread_pool.submit(asyncio.run, send_node_status(client, message, chat_data['node_status_day']))
    elif query == 'cf_return':
        await r_cf_menu(client, message)


# 节点状态按钮回调
@Client.on_callback_query(filters.regex('^gns_'))
async def node_status(client, message):
    query = message.data
    if chat_data['node_status_mode'] == 'menu':
        if query == 'gns_status_down':
            if 'node_status_day' in chat_data and chat_data['node_status_day']:
                chat_data['node_status_day'] += 1
                thread_pool.submit(asyncio.run, send_node_status(client, message, chat_data['node_status_day']))
        elif query == 'gns_status_up':
            chat_data['node_status_day'] -= 1
            thread_pool.submit(asyncio.run, send_node_status(client, message, chat_data['node_status_day']))
    elif chat_data['node_status_mode'] == 'command':
        if query == 'gns_expansion':
            chat_data['packUp'] = not chat_data['packUp']
            thread_pool.submit(asyncio.run, view_bandwidth_button(client, message, chat_data['node_status_day']))
        elif query == 'gns_status_down':
            if 'node_status_day' in chat_data and chat_data['node_status_day']:
                chat_data['node_status_day'] += 1
                thread_pool.submit(asyncio.run, view_bandwidth_button(client, message, chat_data['node_status_day']))
        elif query == 'gns_status_up':
            chat_data['node_status_day'] -= 1
            thread_pool.submit(asyncio.run, view_bandwidth_button(client, message, chat_data['node_status_day']))


# cf账号管理按钮回调
@Client.on_callback_query(filters.regex('account_'))
async def account_button_callback(client, message):
    query = message.data
    if query == 'account_add':
        await account_add(client, message)
        chat_data['ad_message'] = message
    elif query == 'account_return':
        chat_data["account_add"] = False
        await account(client, message)


# 按钮回调 通知设置
@Client.on_callback_query(filters.regex('cronjob_set'))
async def cronjob_set_callback(client, message):
    chat_data["cronjob_set"] = False
    await cronjob_set(client, message)


async def toggle_auto_management(client, message, option, job_id, mode):
    query = message.data
    if query == f'{option}_off':
        cloudflare_cfg['cronjob'][option] = False
        logging.info(f'已关闭{option}')
        cc = cloudflare_cfg['cronjob']
        abc = all(not cc[key] for key in ('status_push', 'storage_mgmt', 'auto_switch_nodes'))
        if abc or option == 'bandwidth_push':
            logging.info('节点监控已关闭')
            aps.pause_job(job_id)
    elif query == f'{option}_on':
        cloudflare_cfg['cronjob'][option] = True
        logging.info(f'已开启{option}')
        aps.resume_job(job_id=job_id)
        if mode == 0:
            aps.add_job(func=send_cronjob_bandwidth_push, args=[client],
                        trigger=CronTrigger.from_crontab(cloudflare_cfg['cronjob']['time']),
                        job_id=job_id)
        elif mode == 1:
            aps.add_job(func=send_cronjob_status_push, args=[client],
                        trigger='interval',
                        job_id=job_id,
                        seconds=60)
    write_config('config/cloudflare_cfg.yaml', cloudflare_cfg)
    await r_cf_menu(client, message)


# 按钮回调 节点状态
@Client.on_callback_query(filters.regex('^status_push'))
async def status_push(client, message):
    await toggle_auto_management(client, message, 'status_push', 'cronjob_status_push', 1)


# 按钮回调 每日带宽统计
@Client.on_callback_query(filters.regex('^bandwidth_push'))
async def bandwidth_push(client, message):
    await toggle_auto_management(client, message, 'bandwidth_push', 'cronjob_bandwidth_push', 0)


# 按钮回调 自动存储管理
@Client.on_callback_query(filters.regex('^storage_mgmt'))
async def storage_mgmt(client, message):
    await toggle_auto_management(client, message, 'storage_mgmt', 'cronjob_status_push', 1)


# 按钮回调 自动切换节点
@Client.on_callback_query(filters.regex('^auto_switch_nodes'))
async def auto_switch_nodes(client, message):
    await toggle_auto_management(client, message, 'auto_switch_nodes', 'cronjob_status_push', 1)


#####################################################################################
#####################################################################################

# 监听普通消息
async def echo_cloudflare(client, message):
    if 'account_add' in chat_data and chat_data["account_add"]:
        await account_edit(client, message)
    elif 'cronjob_set' in chat_data and chat_data["cronjob_set"]:
        await cronjob_set_edit(client, message)
        chat_data["cronjob_set"] = False


def cf_aaa():
    if nodee():
        nodes = [value['url'] for value in nodee()]
        with concurrent.futures.ThreadPoolExecutor() as executor:
            futures = [executor.submit(check_node_status, node) for node in nodes]
        results = [future.result()[1] for future in concurrent.futures.wait(futures).done]
        return f'''
节点数量：{len(nodes)}
🟢  正常：{results.count(200)}
🔴  掉线：{results.count(429)}
⭕️  错误：{results.count(501)}
'''
    return 'Cloudflare节点管理\n暂无账号，请先添加cf账号'


# cf菜单
@Client.on_message(filters.command('sf') & filters.private)
@admin_yz
async def cf_menu(client, message):
    chat_data['cf_menu'] = await client.send_message(chat_id=message.chat.id,
                                                     text='检测节点中...',
                                                     reply_markup=InlineKeyboardMarkup(btn()))
    await client.edit_message_text(chat_id=chat_data['cf_menu'].chat.id,
                                   message_id=chat_data['cf_menu'].id,
                                   text=cf_aaa(),
                                   reply_markup=InlineKeyboardMarkup(btn()))


# 返回菜单
async def r_cf_menu(client, message):
    chat_id, message_id = message.message.chat.id, message.message.id
    await client.edit_message_text(chat_id=chat_id,
                                   message_id=message_id,
                                   text=cf_aaa(),
                                   reply_markup=InlineKeyboardMarkup(btn()))


# 获取节点信息
def get_node_info(url, email, key, zone_id, day):
    d = date_shift(day)
    ga = graphql_api(email, key, zone_id, d[1], d[2])
    ga = json.loads(ga.text)
    byte = ga['data']['viewer']['zones'][0]['httpRequests1dGroups'][0]['sum']['bytes']
    request = ga['data']['viewer']['zones'][0]['httpRequests1dGroups'][0]['sum']['requests']
    code = check_node_status(url)[1]
    if code == 200:
        code = '🟢'
    elif code == 429:
        code = '🔴'
    else:
        code = '⭕️'
    text = f'''
{url} | {code}
请求：<code>{request}</code> | 带宽：<code>{pybyte(byte)}</code>
———————'''

    return text, byte, code, request


# 菜单中的节点状态
@handle_exception
@admin_yz
async def send_node_status(client, message, day):
    chat_id, message_id = message.message.chat.id, message.message.id
    chat_data['node_status_mode'] = 'menu'
    chat_data['node_status_expand'] = False
    chat_data['packUp'] = False
    button = [bandwidth_button_a, bandwidth_button_b, bandwidth_button_c, return_button]
    await client.edit_message_text(chat_id=chat_id,
                                   message_id=message_id,
                                   text='检测节点中...',
                                   reply_markup=InlineKeyboardMarkup(button)
                                   )
    vv = get_node_status(day)
    a = [vv[1], vv[2], vv[3], return_button]

    await client.edit_message_text(chat_id=chat_id,
                                   message_id=message_id,
                                   text=vv[0],
                                   reply_markup=InlineKeyboardMarkup(a)
                                   )


# 使用指令查看节点信息
@Client.on_message(filters.command('vb'))
@handle_exception
async def view_bandwidth(client, message):
    async def view_bandwidth_a(client_a, message_a):
        chat_data['node_status_mode'] = 'command'
        chat_data['packUp'] = True
        chat_data['node_status_expand'] = False
        a = await client_a.send_message(chat_id=message_a.chat.id,
                                        text='检测节点中...')

        day = int(message_a.command[1]) if message_a.command[1:] else 0
        chat_data['node_status_day'] = day
        vv = get_node_status(day)
        state = '🔼点击展开🔼' if chat_data['packUp'] else '🔽点击收起🔽'
        button = [InlineKeyboardButton(state, callback_data='gns_expansion') if 'packUp' in chat_data and chat_data[
            'packUp'] else None]
        text = vv[0]
        button = [button, vv[2], vv[3]] if 'packUp' in chat_data and chat_data['packUp'] else [button, vv[1], vv[2],
                                                                                               vv[3]]
        await client_a.edit_message_text(chat_id=a.chat.id,
                                         message_id=a.id,
                                         text=text,
                                         reply_markup=InlineKeyboardMarkup(button))

    thread_pool.submit(asyncio.run, view_bandwidth_a(client, message))


# view_bandwidth按钮
async def view_bandwidth_button(client, message, day):
    chat_id, message_id = message.message.chat.id, message.message.id
    state = '🔼点击展开🔼' if chat_data['packUp'] else '🔽点击收起🔽'
    ab = [InlineKeyboardButton(state, callback_data='gns_expansion')]
    button = [ab, bandwidth_button_a, bandwidth_button_b, bandwidth_button_c]
    if 'packUp' in chat_data and chat_data['packUp']:
        button = [ab, bandwidth_button_b, bandwidth_button_c]
    await client.edit_message_text(chat_id=chat_id,
                                   message_id=message_id,
                                   text='检测节点中...',
                                   reply_markup=InlineKeyboardMarkup(button)
                                   )
    vv = get_node_status(day)
    text = vv[0]

    button = [ab, vv[2], vv[3]] if 'packUp' in chat_data and chat_data['packUp'] else [ab, vv[1], vv[2], vv[3]]
    await client.edit_message_text(chat_id=chat_id, message_id=message_id, text=text,
                                   reply_markup=InlineKeyboardMarkup(button))


# 获取节点状态
def get_node_status(s):
    d = date_shift(int(s))
    node_list = nodee()
    if not node_list:
        return '请先添加账号', [[InlineKeyboardButton('请先添加账号', callback_data='please_add_an_account_first')]]
    url, email, key, zone_id = zip(*[(n['url'], n['email'], n['global_api_key'], n['zone_id']) for n in node_list])

    def xx(_day):
        with concurrent.futures.ThreadPoolExecutor() as executor:
            futures = [executor.submit(get_node_info, url_, email_, key_, zone_id_, _day) for
                       url_, email_, key_, zone_id_ in
                       zip(url, email, key, zone_id)]
        result_list = []
        for future in concurrent.futures.wait(futures).done:
            with contextlib.suppress(IndexError):
                result_list.append(future.result())
        return result_list

    results = xx(s)
    if not results:
        results, d = xx(-1), date_shift(-1)
        chat_data['node_status_day'] -= 1
    text = [i[0] for i in results]
    text.sort(key=lambda x: x.split(' |')[0])
    text = ''.join(text)
    total_bandwidth = sum(i[1] for i in results)
    code = [i[2] for i in results]
    request = f'{int(sum(i[3] for i in results) / 10000)}W'

    text = f'''
节点数量：{len(code)}
🟢  正常：{code.count('🟢')}
🔴  掉线：{code.count('🔴')}
⭕️  错误：{code.count('⭕️')}
    ''' if 'packUp' in chat_data and chat_data['packUp'] else text

    button_b = [
        InlineKeyboardButton(
            f"🟢{code.count('🟢')}", callback_data='gns_total_bandwidth'
        ),
        InlineKeyboardButton(
            f"🔴{code.count('🔴')}", callback_data='gns_total_bandwidth'
        ),
        InlineKeyboardButton(
            f"⭕️{code.count('⭕️')}", callback_data='gns_total_bandwidth'
        ),
    ]
    button_c = [
        InlineKeyboardButton(
            f'📊总请求：{request}', callback_data='gns_total_bandwidth'
        ),
        InlineKeyboardButton(
            f'📈总带宽：{pybyte(total_bandwidth)}',
            callback_data='gns_total_bandwidth',
        ),
    ]
    button_d = [
        InlineKeyboardButton('🔙上一天', callback_data='gns_status_up'),
        InlineKeyboardButton(d[0], callback_data='gns_status_calendar'),
        InlineKeyboardButton('下一天🔜', callback_data='gns_status_down'),
    ]

    return text, button_b, button_c, button_d, code


# 账号管理

async def account(client, message):
    chat_id, message_id = message.message.chat.id, message.message.id
    text = []
    button = [
        InlineKeyboardButton('编辑', callback_data='account_add')
    ]
    if nodee():
        for index, value in enumerate(nodee()):
            text_t = f"{index + 1} | <code>{value['email']}</code> | <code>{value['url']}</code>\n"
            text.append(text_t)
        t = '\n'.join(text)
    else:
        t = '暂无账号'
    await client.edit_message_text(chat_id=chat_id,
                                   message_id=message_id,
                                   text=t,
                                   reply_markup=InlineKeyboardMarkup([button, return_button]))


# 添加/删除账号
async def account_add(client, message):
    chat_id, message_id = message.message.chat.id, message.message.id
    text = []
    chat_data['account_add_return_button'] = [
        InlineKeyboardButton('↩️返回账号', callback_data='account_return'),
        InlineKeyboardButton('❌关闭菜单', callback_data='cf_close'),
    ]
    if nodee():
        for index, value in enumerate(nodee()):
            text_t = f"{index + 1} | <code>{value['email']}</code> | <code>{value['global_api_key']}</code>\n"
            text.append(text_t)
        t = '\n'.join(text)
    else:
        t = '暂无账号'
    tt = '''
——————————————
<b>添加：</b>
一次只能添加一个账号
第一行cf邮箱，第二行global_api_key，例：
<code>abc123@qq.com
285812f3012365412d33398713c156e2db314
</code>
<b>删除：</b>
*+序号，例：<code>*2</code>
'''
    await client.edit_message_text(chat_id=chat_id,
                                   message_id=message_id,
                                   text=t + tt,
                                   reply_markup=InlineKeyboardMarkup([chat_data['account_add_return_button']]))
    chat_data["account_add"] = True


# 开始处理
async def account_edit(client, message):
    mt = message.text
    await client.delete_messages(chat_id=message.chat.id, message_ids=message.id)
    if mt[0] != '*':
        try:
            i = mt.split('\n')

            lz = list_zones(i[0], i[1])  # 获取区域id
            lz = json.loads(lz.text)
            account_id = lz['result'][0]['account']['id']
            zone_id = lz['result'][0]['id']
            lf = list_filters(i[0], i[1], zone_id)  # 获取url
            lf = json.loads(lf.text)
        except Exception as e:
            await chat_data['ad_message'].answer(text=f'错误：{str(e)}')
        else:
            if lf['result']:
                url = lf['result'][0]['pattern'].rstrip('/*')
                d = {"url": url, "email": i[0], "global_api_key": i[1], "account_id": account_id, "zone_id": zone_id}
                if cloudflare_cfg['node']:
                    cloudflare_cfg['node'].append(d)
                else:
                    cloudflare_cfg['node'] = [d]
                write_config("config/cloudflare_cfg.yaml", cloudflare_cfg)
                await account_add(client, chat_data['ad_message'])
            else:
                text = f"""
<b>添加失败: </b>

<code>{mt}</code>

该域名（<code>{lz['result'][0]['name']}</code>）未添加Workers路由
请检查后重新发送账号

<b>注：</b>默认使用第一个域名的第一个Workers路由
"""
                await client.edit_message_text(chat_id=chat_data['ad_message'].message.chat.id,
                                               message_id=chat_data['ad_message'].message.id,
                                               text=text,
                                               reply_markup=InlineKeyboardMarkup(
                                                   [chat_data['account_add_return_button']]))

    else:
        i = int(mt.split('*')[1])
        del cloudflare_cfg['node'][i - 1]
        write_config("config/cloudflare_cfg.yaml", cloudflare_cfg)
        await account_add(client, chat_data['ad_message'])


# 通知设置
async def cronjob_set(client, message):
    chat_id, message_id = message.message.chat.id, message.message.id
    text = f"""
chat_id: <code>{",".join(list(map(str, cronjob()['chat_id']))) if cronjob()['chat_id'] else None}</code>
time: <code>{cronjob()['time'] or None}</code>
——————————
chat_id 可以填用户/群组/频道 id，支持多个，用英文逗号隔开

time 为带宽通知时间，格式为5位cron表达式

chat_id 和 time 一行一个，例：
<code>123123,321321
0 23 * * *</code>
"""

    await client.edit_message_text(chat_id=chat_id,
                                   message_id=message_id,
                                   text=text,
                                   reply_markup=InlineKeyboardMarkup([return_button]))
    chat_data["cronjob_set"] = True


# 通知设置
async def cronjob_set_edit(client, message):
    chat_id, message_id = chat_data['cf_menu'].chat.id, chat_data['cf_menu'].id
    d = message.text
    dd = d.split('\n')
    cloudflare_cfg['cronjob']['chat_id'] = [int(x) for x in dd[0].split(',')]
    cloudflare_cfg['cronjob']['time'] = dd[1]
    if cloudflare_cfg['cronjob']['bandwidth_push']:
        aps.modify_job(trigger=CronTrigger.from_crontab(cloudflare_cfg['cronjob']['time']),
                       job_id='cronjob_bandwidth_push')
    write_config('config/cloudflare_cfg.yaml', cloudflare_cfg)
    await client.delete_messages(chat_id=message.chat.id, message_ids=message.id)
    await client.edit_message_text(chat_id=chat_id,
                                   message_id=message_id,
                                   text=f"设置成功！\n-------\nchat_id：<code>{cloudflare_cfg['cronjob']['chat_id']}</code>"
                                        f"\ntime：<code>{cloudflare_cfg['cronjob']['time']}</code>",
                                   reply_markup=InlineKeyboardMarkup([return_button]))


# 带宽通知定时任务
async def send_cronjob_bandwidth_push(app):
    chat_data['packUp'] = True
    chat_data['node_status_expand'] = False
    vv = get_node_status(0)
    text = '今日流量统计'
    for i in cloudflare_cfg['cronjob']['chat_id']:
        await app.send_message(chat_id=i,
                               text=text,
                               reply_markup=InlineKeyboardMarkup([vv[1], vv[2]]))


# 节点状态通知定时任务
async def send_cronjob_status_push(app):  # sourcery skip: low-code-quality
    if not nodee():
        return

    async def run():
        nodes = [value['url'] for value in nodee()]
        with concurrent.futures.ThreadPoolExecutor() as executor:
            futures = [executor.submit(check_node_status, node) for node in nodes]
        # 全部节点
        results = [future.result() for future in concurrent.futures.wait(futures).done]

        available_nodes = []
        if cloudflare_cfg['cronjob']['auto_switch_nodes']:
            # 筛选出可用的节点
            node_pool = [f'https://{node}' for node, result in results if result == 200]
            # 已经在使用的节点
            sl = json.loads(storage_list().text)['data']['content']
            used_node = [node['down_proxy_url'] for node in sl if
                         node['webdav_policy'] == 'use_proxy_url' or node['web_proxy']]
            # 将已用的节点从可用节点中删除
            available_nodes = [x for x in node_pool if x not in used_node]

        for node, result in results:
            if node not in chat_data:
                chat_data[node] = result
                chat_data[f'{node}_count'] = 0

            if result == 200:
                text_a = f'🟢|{node}|恢复'
            elif result == 429:
                text_a = f'🔴|{node}|掉线'
                chat_data[f'{node}_count'] += 1
            else:
                text_a = f'⭕️|{node}|故障'
                chat_data[f'{node}_count'] += 1

            # 错误大于3次运行，否则不运行后面代码
            if result != 200 and 0 < chat_data[f'{node}_count'] <= 3:
                break

            if result != chat_data[node]:
                chat_data[f'{node}_count'] = 0
                # 状态通知
                if cloudflare_cfg['cronjob']['status_push']:
                    chat_data[node] = result
                    for i in cloudflare_cfg['cronjob']['chat_id']:
                        await app.send_message(chat_id=i, text=text_a)

                # 自动管理
                chat_data[node] = result
                st = storage_list()
                st = json.loads(st.text)
                for dc in st['data']['content']:
                    if dc['down_proxy_url'] == f'https://{node}' and (
                            dc['webdav_policy'] == 'use_proxy_url' or dc['web_proxy']):
                        if result == 200 and dc['disabled']:
                            storage_enable(dc['id'])
                            text_b = f'🟢|{node}|已开启存储：<code>{dc["mount_path"]}</code>'
                            logging.info(text_b)
                            await app.send_message(chat_id=admin, text=text_b)
                        elif result == 429 and not dc['disabled']:
                            if available_nodes:
                                dc['down_proxy_url'] = available_nodes[0]
                                d = available_nodes[0].replace('https://', '')
                                if '节点：' in dc['remark']:
                                    lines = dc['remark'].split('\n')
                                    lines = [f"节点：{d}" if '节点：' in line else line for line in lines]
                                    dc['remark'] = '\n'.join(lines)
                                else:
                                    dc['remark'] = f"节点：{d}\n{dc['remark']}"
                                storage_update(dc)
                                a = available_nodes[0].replace("https://", "")
                                text = f'🟡|<code>{dc["mount_path"]}</code>\n已自动切换节点： {node} --> {a}'
                                logging.info(text)
                                await app.send_message(chat_id=admin,
                                                       text=text,
                                                       disable_web_page_preview=True)
                            elif cloudflare_cfg['cronjob']['storage_mgmt']:
                                storage_disable(dc['id'])
                                text = f'🔴|{node}|已关闭存储：<code>{dc["mount_path"]}</code>'
                                logging.info(text)
                                await app.send_message(chat_id=admin,
                                                       text=text,
                                                       disable_web_page_preview=True)

    thread_pool.submit(asyncio.run, run())


#####################################################################################
#####################################################################################
# 检查节点状态
def check_node_status(url):
    status_code_map = {
        200: [url, 200],
        429: [url, 429],
    }
    try:
        response = requests.get(f'https://{url}')
        return status_code_map.get(response.status_code, [url, 502])
    except Exception as e:
        logging.error(e)
        return [url, 501]


# 将当前日期移位n天，并返回移位日期和移位日期的前一个和下一个日期。
def date_shift(n: int = 0):
    today = datetime.date.today()
    shifted_date = datetime.date.fromordinal(today.toordinal() + n)
    previous_date = datetime.date.fromordinal(shifted_date.toordinal() - 1)
    next_date = datetime.date.fromordinal(shifted_date.toordinal() + 1)
    previous_date_string = previous_date.isoformat()
    next_date_string = next_date.isoformat()
    return shifted_date.isoformat(), previous_date_string, next_date_string
