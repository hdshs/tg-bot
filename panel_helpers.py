from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def whitelist_user_buttons(target_user_id: int):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("开启权限", callback_data=f"wl_enable|{target_user_id}"),
            InlineKeyboardButton("关闭权限", callback_data=f"wl_disable|{target_user_id}")
        ],
        [
            InlineKeyboardButton("延长7天", callback_data=f"wl_extend|{target_user_id}|7"),
            InlineKeyboardButton("延长30天", callback_data=f"wl_extend|{target_user_id}|30")
        ],
        [
            InlineKeyboardButton("减少1天", callback_data=f"wl_reduce|{target_user_id}|1"),
            InlineKeyboardButton("减少7天", callback_data=f"wl_reduce|{target_user_id}|7")
        ],
        [
            InlineKeyboardButton("群额度+1", callback_data=f"wl_limit_add|{target_user_id}"),
            InlineKeyboardButton("群额度-1", callback_data=f"wl_limit_sub|{target_user_id}")
        ],
        [InlineKeyboardButton("删除白名单", callback_data=f"wl_delete|{target_user_id}")],
        [InlineKeyboardButton("返回白名单列表", callback_data="wl_list")],
        [InlineKeyboardButton("返回主菜单", callback_data="back_main")]
    ])


def fixed_time_menu_panel(chat_id: str, items: list):
    rows = [
        [InlineKeyboardButton("添加固定时间", callback_data=f"ad_fixed_add|{chat_id}")]
    ]

    if items:
        for item in items:
            label = f"{item['time_text']}｜{'开' if item['enabled'] else '关'}"
            rows.append([
                InlineKeyboardButton(label, callback_data=f"ad_fixed_open|{chat_id}|{item['id']}")
            ])
    else:
        rows.append([InlineKeyboardButton("暂无固定时间", callback_data="noop")])

    rows.append([InlineKeyboardButton("返回广告管理", callback_data=f"ad_menu|{chat_id}")])
    return InlineKeyboardMarkup(rows)


def fixed_time_item_panel(chat_id: str, item_id: int):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("启用", callback_data=f"ad_fixed_enable|{chat_id}|{item_id}"),
            InlineKeyboardButton("停用", callback_data=f"ad_fixed_disable|{chat_id}|{item_id}")
        ],
        [InlineKeyboardButton("删除时间", callback_data=f"ad_fixed_delete|{chat_id}|{item_id}")],
        [InlineKeyboardButton("返回固定时间列表", callback_data=f"ad_fixed_menu|{chat_id}")]
    ])
