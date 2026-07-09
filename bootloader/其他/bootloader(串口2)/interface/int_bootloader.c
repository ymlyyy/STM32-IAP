#include "int_bootloader.h"

uint8_t uart_receive_buff[BOOTLOADER_UART_REC_BUFF_LEN] = {0};
volatile  uint8_t receive_flag = 0;
volatile  uint16_t receive_len = 0;
volatile  uint16_t receive_len_total = 0;



void Int_Bootloader_init(void)
{
    __HAL_UART_CLEAR_IDLEFLAG(&huart2);   // 清除空闲标志
    __HAL_UART_CLEAR_OREFLAG(&huart2);    // 清除溢出错误标志
    // 带中断的接收函数，接收数据到空闲中断
    HAL_UARTEx_ReceiveToIdle_IT(&huart2, uart_receive_buff, BOOTLOADER_UART_REC_BUFF_LEN);
}

void HAL_UARTEx_RxEventCallback(UART_HandleTypeDef *huart, uint16_t Size)
{
    if(huart->Instance == USART1)
    {
        receive_len = Size;
        receive_len_total += receive_len;

        receive_flag = 1;		


        __HAL_UART_CLEAR_IDLEFLAG(&huart2);   // 清除空闲标志
        __HAL_UART_CLEAR_OREFLAG(&huart2);    // 清除溢出错误标志
        HAL_UARTEx_ReceiveToIdle_IT(&huart2, uart_receive_buff, BOOTLOADER_UART_REC_BUFF_LEN);
    }
}
