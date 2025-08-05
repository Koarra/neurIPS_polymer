import pandas as pd
import numpy as np
from rdkit import Chem
from rdkit.Chem import Descriptors
from sklearn.model_selection import train_test_split
from sklearn.metrics import mean_absolute_error
import xgboost as xgb

# === 1. Featurization function ===
def featurize_smiles(smiles):
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        return None
    return {
        'MolWt': Descriptors.MolWt(mol),
        'MolLogP': Descriptors.MolLogP(mol),
        'TPSA': Descriptors.TPSA(mol),
        'NumRings': Descriptors.RingCount(mol),
        'NumHDonors': Descriptors.NumHDonors(mol),
        'NumHAcceptors': Descriptors.NumHAcceptors(mol),
        'NumRotatableBonds': Descriptors.NumRotatableBonds(mol),
        'NumAtoms': mol.GetNumAtoms(),
        'NumBonds': mol.GetNumBonds(),
    }

# === 2. Load & preprocess training data ===

# Featurize SMILES, drop invalid
feature_list, valid_idx = [], []
for i, smi in enumerate(df['SMILES']):
    f = featurize_smiles(smi)
    if f:
        feature_list.append(f)
        valid_idx.append(i)
features_df = pd.DataFrame(feature_list)
df = df.loc[valid_idx].reset_index(drop=True)
df = pd.concat([df, features_df], axis=1)

# === 3. Define targets & containers ===
target_cols = ['Tg', 'FFV', 'Tc', 'Density', 'Rg']
models = {}
results = {}

# === 4. Train one XGBoost per property ===
for col in target_cols:
    sub = df[df[col].notna()]
    if len(sub) < 20:
        print(f"Skipping {col}: too few samples.")
        continue
    X, y = sub[features_df.columns], sub[col]
    X_tr, X_val, y_tr, y_val = train_test_split(X, y, test_size=0.2, random_state=42)
    dtrain = xgb.DMatrix(X_tr, label=y_tr)
    dval   = xgb.DMatrix(X_val, label=y_val)
    params = {
        "objective": "reg:squarederror",
        "eval_metric": "mae",
        "max_depth": 6,
        "eta": 0.05,
        "subsample": 0.9,
        "colsample_bytree": 0.9,
        "seed": 42
    }
    model = xgb.train(
        params=params,
        dtrain=dtrain,
        num_boost_round=300,
        evals=[(dval, "eval")],
        early_stopping_rounds=20,
        verbose_eval=False
    )
    y_pred = model.predict(dval)
    mae = mean_absolute_error(y_val, y_pred)
    print(f"{col} MAE: {mae:.4f}")
    models[col] = model
    results[col] = {"y_val": y_val, "y_pred": y_pred, "mae": mae}

# === 5. (Optional) Compute wMAE ===
def compute_wmae(results_dict, full_df, target_cols):
    K = len(target_cols)
    n_i = {c: len(results_dict[c]['y_val']) for c in results_dict}
    r_i = {c: full_df[c].max() - full_df[c].min() for c in results_dict}
    raw_mae = {c: results_dict[c]['mae'] for c in results_dict}
    inv_sqrt_ni = {c: np.sqrt(1/n_i[c]) for c in n_i}
    denom = sum(inv_sqrt_ni.values())
    scale = {c: K * inv_sqrt_ni[c] / denom for c in n_i}
    weights = {c: (1/r_i[c]) * scale[c] for c in n_i}
    wmae = sum(weights[c] * raw_mae[c] for c in n_i)
    print("\n--- wMAE ---")d
    for c in n_i:
        print(f"{c}: MAE={raw_mae[c]:.4f}, w={weights[c]:.4f}")
    print(f"Final wMAE: {wmae:.5f}")
    return wmae

wmae_score = compute_wmae(results, df, target_cols)

# === 6. Load & preprocess test data ===
test_df['index'] = test_df.index
tf_list = []
for _, row in test_df.iterrows():
    f = featurize_smiles(row['SMILES'])
    if f:
        f['id'] = row['id']
        f['index'] = row['index']
        tf_list.append(f)
test_feat_df = pd.DataFrame(tf_list)

# === 7. Predict & build submission ===
submission = test_df[['id']].copy()
for col in target_cols:
    if col not in models:
        submission[col] = 0.0
        continue
    dtest = xgb.DMatrix(test_feat_df[features_df.columns])
    preds = models[col].predict(dtest)
    tmp = pd.DataFrame({
        'index': test_feat_df['index'],
        col: preds
    })
    merged = test_df[['index']].merge(tmp, on='index', how='left')
    submission[col] = merged[col].fillna(0.0)

# === 8. Validate submission format ===
assert len(submission) == len(test_df), "Row count mismatch!"
expected = ['id'] + target_cols
assert list(submission.columns) == expected, f"Columns: {submission.columns}"
assert not submission.isna().any().any(), "NaNs found!"
submission = submission.astype({c: float for c in target_cols})
submission['id'] = submission['id'].astype(int)
print("✅ Submission format OK")

# === 9. Save to CSV ===
submission.to_csv("submission.csv", index=False)
print("✅ submission.csv written successfully")
