# Debug 心路历程

## 2026-07-09

### 问题

Bootloader 跳转 App（`0x08002800`）后 LED 不闪。App 直接烧 `0x08000000` 正常。

### 排查

起初以为是 App 初始化问题，加了一圈东西（开中断、更新时钟、改栈顶偏移），都没解决本质。回头看 Bootloader 跳转函数。

### 根因

`Int_bootloader_jump_to_app()` 跳转前调了 `__set_MSP(app_stack_prt)`。

```
CMSIS __set_MSP 实现：
    register uint32_t __regMainStackPointer __ASM("msp");
    __regMainStackPointer = topOfMainStack;
```

编译器认为这只是一条普通的寄存器赋值，不知道 `msp` 在被赋值后变成了栈指针。函数收尾照常生成 `ldmia sp!, {r4,r5,r6,lr}` 来恢复入口压栈的 4 个寄存器：

```asm
push  {r4, r5, r6, lr}     ; 函数入口
...
msr   MSP, r5               ; __set_MSP → 编译器不知道在改 SP
...
ldmia sp!, {r4, r5, r6, lr} ; 从新 SP（App 栈顶）读 16 字节 → 越界 → BusFault
bx    ip
```

### 修复

把跳转单独写成纯汇编函数，MSR 之后不经过任何 C 代码，直接 BX：

```c
__asm void boot_jump(uint32_t sp, uint32_t pc) {
    MSR msp, r0
    BX r1
}
```

### 教训

局部寄存器变量 `register ... __ASM("msp")` 对编译器是不可见的副作用。编译器不会因为这个变量被赋值就去调整栈管理代码。涉及修改 MSP/PSP 的跳转，必须用独立汇编函数隔开。
