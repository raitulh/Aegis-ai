# Training models outside the platform

DataBattles never trains models and needs no GPU. Participants train wherever they like and upload **prediction files**.

## Workflow

1. Join the competition and accept the rules.
2. Download `train.csv`, `test.csv` and `sample_submission.csv` from the competition's **Data** tab (signed links,
   valid for a few minutes; accept dataset terms first if required).
3. Train anywhere:
   * **Google Colab** — upload the files or mount Drive; free GPUs are available there if your model needs one.
   * **Kaggle Notebooks** — add the files as a private dataset.
   * **Your laptop** — scikit-learn/XGBoost handle the demo datasets in seconds on a CPU.
4. Write predictions for every id in `test.csv` using the exact column names shown on the submission page.
5. Upload the CSV on the **Submissions** tab. It is validated immediately in a sandbox; errors are reported per row and
   do not use your daily quota.

## Minimal example (scikit-learn)

```python
import pandas as pd
from sklearn.ensemble import GradientBoostingRegressor

train = pd.read_csv("train.csv")
test = pd.read_csv("test.csv")
features = ["rainfall_mm", "avg_temp_c", "soil_ph", "fertilizer_kg_ha"]
X = pd.get_dummies(train[features + ["region"]])
Xt = pd.get_dummies(test[features + ["region"]]).reindex(columns=X.columns, fill_value=0)
model = GradientBoostingRegressor(random_state=0).fit(X, train["yield_t_ha"])
pd.DataFrame({"id": test["id"], "yield_t_ha": model.predict(Xt)}).to_csv("submission.csv", index=False)
```

## Tips

* Keep a validation split — the public leaderboard uses only part of the test set; final ranking uses the hidden rest.
* Select your final submissions before the deadline (otherwise your best public ones are used).
* Link notebooks as *starter assets* or projects to share your approach after the competition.
