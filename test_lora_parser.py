from transformers import AutoModelForCausalLM, AutoTokenizer
from peft import PeftModel
import torch

adapter_path = "lfm25-jp-duelmasters-final"
base_model = "LiquidAI/LFM2.5-1.2B-JP-202606"
device = "cuda" if torch.cuda.is_available() else "cpu"
dtype = torch.float16 if torch.cuda.is_available() else torch.float32

tokenizer = AutoTokenizer.from_pretrained(adapter_path)
if tokenizer.pad_token is None:
    tokenizer.pad_token = tokenizer.eos_token

model = AutoModelForCausalLM.from_pretrained(
    base_model,
    dtype=dtype,
    low_cpu_mem_usage=False,
)
model = PeftModel.from_pretrained(model, adapter_path, is_trainable=False)
model = model.to(device)
model.eval()

messages = [
    {"role": "system", "content": "You are an expert Duel Masters card game rules engine parser. Return valid JSON only."},
    {"role": "user", "content": "Parse this card text:\nCard: Test Card\nText: When this creature attacks, draw a card."},
]

prompt = tokenizer.apply_chat_template(
    messages, tokenize=False, add_generation_prompt=True)
inputs = tokenizer(prompt, return_tensors="pt").to(device)

with torch.no_grad():
    output = model.generate(
        **inputs,
        max_new_tokens=512,
        do_sample=False,
    )

print(tokenizer.decode(
    output[0][inputs["input_ids"].shape[-1]:], skip_special_tokens=True))
