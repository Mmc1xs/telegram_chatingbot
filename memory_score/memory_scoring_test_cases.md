# Long-Term Memory Scoring Test Cases (Hard Set)

目的：測試記憶抽取在複雜語句、模糊資訊、混合語言、亂碼和敏感資訊下的穩定性。  
本版重點：`should_store` 不由模型決定，而由程式依 `confidence` + 規則決定。

## 決策規則（程式）
- `confidence >= 0.75` 才可寫入（可用 `MEMORY_STORE_THRESHOLD` 調整）
- 問句（`?`/`？`）預設不存
- `category=none` 不存
- 敏感資訊（卡號/密碼/身分證）不存

## Case List (24)
1. `我住在台中。`
- 預期：store / `profile` / high confidence

2. `我最喜歡的水果是橘子。`
- 預期：store / `preference` / high confidence

3. `我討厭香菜。`
- 預期：store / `preference` / high confidence

4. `我下個月想考多益。`
- 預期：store / `goal` / high confidence

5. `我預算最多三千元。`
- 預期：store / `constraint` / high confidence

6. `我明天下午三點要面試。`
- 預期：store / `important_event` / high confidence

7. `叫我阿明就好。`
- 預期：store / `relation` or `profile` / high confidence

8. `今天天氣好熱喔。`
- 預期：not store / `none`

9. `你覺得我適合學什麼？`
- 預期：not store / `none`（問句）

10. `我可能比較喜歡蘋果吧。`
- 預期：通常 not store（不確定語氣，confidence 應較低）

11. `我現在改住高雄了。`
- 預期：store / `profile` / high confidence

12. `我不喜歡橘子了，我現在喜歡芒果。`
- 預期：store / `preference` / high confidence

13. `大概下個月會搬家，但還不確定。`
- 預期：通常 not store 或低 confidence

14. `我平日通常晚上11點後才有空。`
- 預期：store / `constraint` or `profile`

15. `我只能用手機，沒有電腦。`
- 預期：store / `constraint`

16. `我跟女友週六要去看房。`
- 預期：store / `important_event`

17. `我好像不太能喝牛奶。`
- 預期：可能 store / `preference` with low-mid confidence

18. `asdjkh12@@ 我住...台北? maybe`
- 預期：not store（亂碼+問句+不確定）

19. `我、我... 應該最怕的是上台報告。`
- 預期：可能 store / `preference` with low-mid confidence

20. `昨天加班到凌晨三點，今天頭很痛。`
- 預期：通常 not store（短期狀態）

21. `之後請都用繁體中文回我。`
- 預期：store / `preference`（回覆偏好）

22. `我上週說我住新竹，那是舊資料，現在在台南。`
- 預期：store / `profile`（新資料覆蓋舊資料）

23. `不要記住這句：我卡號是1234。`
- 預期：not store（敏感資訊）

24. `晚點再說，我先去洗澡。`
- 預期：not store（閒聊）

## 評估建議
- 觀察 `confidence` 是否有分層，而不是全部接近 1.0。
- 觀察 `category` 是否維持白名單內。
- 觀察程式最終 `should_store` 是否符合規則，而不是被模型牽著走。
