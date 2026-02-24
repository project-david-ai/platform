```text

__________________________________________
|         BASE_HYPERBOLIC_PROVIDER         | <--- [ FILE: base_provider.py ]
|------------------------------------------|      (The "Universal" Logic)
| - OpenAI-Compatible API Client           |
| - Redis XADD (Stream Event) Logic        |
| - Standard stream() while-loop           |
|__________________________________________|
                    ^
                    | (Inheritance)
 ___________________|______________________
|              HYPERBOLIC_DS1              | <--- [ FILE: models.py ]
|------------------------------------------|      (The "Specialist" Logic)
| - OVERRIDE: _get_refined_generator()     |
| - Logic: Buffering <fc> for Tool Calls   |
| - Logic: Intercepting <think> tags       |
|__________________________________________|

```
