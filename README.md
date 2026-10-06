# 穿岩直升機

橫向捲動的洞穴飛行遊戲。直升機會自動往前飛，按住上升、放開下降，閃過洞頂、洞底與隨機出現的岩柱，挑戰最遠飛行距離。

## 怎麼執行

用瀏覽器直接打開 `index.html`：

```bash
open index.html
```

## 操作

- 按住畫面 / 空白鍵 / ↑ / W：上升；放開：下降
- `P` / `Esc`：暫停

## 道具

- **護盾**（青色）：擋下一次碰撞，撞到岩柱時會把岩柱撞碎
- **慢動作**（紫色）：5 秒內整個世界變慢
- **縮小**（黃色）：7 秒內機身變小，比較容易鑽過縫隙

## 程式結構

全部在 `index.html`，重點函式：

- `ensureTerrain()`：隨機產生洞頂、洞底與岩柱
- `update()`：每幀的物理（重力、推力）、前進與碰撞判定
- `draw()`：繪製洞穴、直升機、煙霧與爆炸
- `ITEMS`：道具種類、出現比重與持續時間
- 檔案開頭的 `GRAVITY`、`LIFT`、`gapAt()` 可以用來調整手感和難度

## 線上排行榜

後端在 `server/`，跑在 AWS EC2（只用 Python 內建模組，不需安裝套件）：

- `server/game_api.py`：API（遊玩次數、前 10 名、上傳成績與基本防作弊）
- `server/game-api.service`：systemd 設定（port 8002，記憶體上限 64MB）
- 對外網址：`https://hoho-game.duckdns.org/game-api/`（Caddy 反向代理）

更新後端：

```bash
scp -i ~/.ssh/stock-key server/game_api.py ubuntu@13.231.218.149:/home/ubuntu/game-api/
ssh -i ~/.ssh/stock-key ubuntu@13.231.218.149 "sudo systemctl restart game-api"
```
